"""
Evidence Quality Gate — flexible, LLM-led.

Design goals:
- LLM makes the relevance/sufficiency judgment (pass + confidence + reason + gaps).
- No keyword rules / string checks to decide PASS.
- "Hard requirements" are enforced deterministically ONLY by reading the LLM's own
  coverage booleans (contract-style). This keeps flexibility while preventing
  silent override.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk
from app.services.evidence_hygiene import compute_hygiene

logger = get_logger(__name__)


@dataclass
class QualityReport:
    """Explainable quality report from LLM."""
    quality_score: float  # 0–1 (LLM confidence)
    feature_scores: dict[str, float]
    missing_signals: list[str]  # "gaps" from LLM
    staleness_risk: float | None
    boilerplate_risk: float | None
    sufficiency_scores: dict[str, float] | None = None
    hard_requirement_coverage: dict[str, bool] | None = None
    gate_pass: bool | None = None
    reason: str | None = None


EVIDENCE_QUALITY_PROMPT = """You judge whether the provided evidence is sufficient to answer the query.

Output MUST be exactly this JSON object. No markdown, no code fences, no extra text.
{
  "is_sufficient": true,
  "confidence": 0.8,
  "reason": "Brief reason.",
  "gaps": [],
  "coverage": {}
}

Field rules (strict):
- is_sufficient: boolean true or false only. true = evidence allows a definitive answer (yes or no). false = evidence vague, silent on topic, or contradictory.
- confidence: number 0.0 to 1.0
- reason: one short sentence
- gaps: array of strings, empty when is_sufficient=true
- coverage: object mapping requirement names to boolean. Use hint's required_evidence keys if provided.

Critical: When evidence explicitly states the answer (e.g. "X is non-refundable", "Y is eligible"), set is_sufficient=true. The verdict does not depend on whether the answer is yes or no.
- For how-to queries: when evidence contains a guide/steps for the requested topic (e.g. "How to change SSH port on Linux" for "change port on Linux VPS"), set is_sufficient=true.
"""


def _build_fail_report(hard_requirements: list[str] | None) -> QualityReport:
    hard_reqs = list(dict.fromkeys(hard_requirements or []))
    hard_coverage = {req: False for req in hard_reqs}
    return QualityReport(
        quality_score=0.0,
        feature_scores={},
        missing_signals=["missing_evidence"] if hard_reqs else ["missing_evidence"],
        staleness_risk=None,
        boilerplate_risk=None,
        sufficiency_scores=None,
        hard_requirement_coverage=hard_coverage,
        gate_pass=False,
        reason="No evidence chunks provided.",
    )


def _extract_probable_json(text: str) -> str:

    s = (text or "").strip()

    # Strip simple code fences if present
    if s.startswith("```"):
        # remove first fence line
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        # remove trailing fence
        last = s.rfind("```")
        if last != -1:
            s = s[:last].strip()

    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        return s

    start = s.find("{")
    end = s.rfind("}")
    if 0 <= start < end:
        return s[start : end + 1].strip()

    return s  # last resort


def _coerce_bool(v: Any) -> bool | None:
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        s = v.strip().lower()
        if s in ("true", "1", "yes"):
            return True
        if s in ("false", "0", "no"):
            return False
    return None


def _coerce_float(v: Any, default: float) -> float:
    try:
        x = float(v)
        if x < 0.0:
            return 0.0
        if x > 1.0:
            return 1.0
        return x
    except Exception:
        return default


def _to_str_list(v: Any) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if x is not None]
    return [str(v)]


async def evaluate_quality(
    query: str,
    chunks: list[EvidenceChunk],
    required_evidence: list[str] | None = None,
    hard_requirements: list[str] | None = None,
    product_type: str | None = None,
) -> QualityReport:
    hard_reqs = list(dict.fromkeys(hard_requirements or []))
    reqs = list(dict.fromkeys(required_evidence or []))

    if not chunks:
        return _build_fail_report(hard_reqs)

    summaries: list[str] = []
    for i, c in enumerate(chunks[:12], 1):
        text = (c.full_text or c.snippet or "").strip()
        text = text[:1600]
        src = (c.source_url or "?").strip()
        summaries.append(f"[{i}] {src}: {text}")

    user_content = f"Query: {query[:600]}\n\nEvidence:\n" + "\n".join(summaries)

    hint: dict[str, Any] = {}
    if reqs:
        hint["required_evidence"] = reqs
    if hard_reqs:
        hint["hard_requirements"] = hard_reqs
    if product_type:
        hint["product_type"] = product_type

    if hint:
        user_content += "\n\nHint (query context): " + json.dumps(hint, ensure_ascii=False)

    try:
        from app.core.tracing import current_llm_task_var
        from app.services.llm_gateway import get_llm_gateway
        from app.services.model_router import get_model_for_task

        current_llm_task_var.set("evidence_quality")
        llm = get_llm_gateway()
        model = get_model_for_task("evidence_quality")

        resp = await llm.chat(
            messages=[
                {"role": "system", "content": EVIDENCE_QUALITY_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            model=model,
            max_tokens=256,
        )

        raw = (resp.content or "").strip()
        text = _extract_probable_json(raw)
        data = json.loads(text)

        # Debug: log raw LLM output when tracing evidence_quality verdict
        logger.debug(
            "evidence_quality_llm_raw",
            raw_preview=raw[:500] if raw else "",
            parsed=data,
            query_preview=(query or "")[:60],
        )

        llm_pass = _coerce_bool(data.get("is_sufficient", data.get("pass")))
        confidence = _coerce_float(data.get("confidence"), default=0.5)
        reason = str(data.get("reason") or "").strip() or None

        # Override: when reason says evidence answers the query but LLM returned is_sufficient=false
        reason_lower = (reason or "").lower()
        suggests_sufficient = any(
            phrase in reason_lower
            for phrase in (
                "directly answers",
                "directly addresses",
                "clearly answers",
                "explicitly states",
                "explicitly answers",
            )
        )
        if suggests_sufficient and llm_pass is False:
            logger.info(
                "evidence_quality_contradiction_override",
                reason=reason,
                original_is_sufficient=False,
                override_to=True,
                query_preview=(query or "")[:80],
            )
            llm_pass = True

        gaps = _to_str_list(data.get("gaps"))
        coverage_raw = data.get("coverage") or {}
        if not isinstance(coverage_raw, dict):
            coverage_raw = {}

        # Only accept boolean coverage values
        coverage: dict[str, bool] = {}
        for k, v in coverage_raw.items():
            if isinstance(k, str) and isinstance(v, bool):
                coverage[k] = v

        # Ensure hard req keys exist (missing => False)
        hard_coverage = {req: bool(coverage.get(req, False)) for req in hard_reqs}

        # Hygiene signals (telemetry only; not used to flip pass)
        boilerplate_risk: float | None = None
        try:
            sigs = compute_hygiene(chunks)
            boilerplate_risk = round((sigs.pct_chunks_boilerplate_gt_06 or 0.0) / 100.0, 3)
        except Exception:
            boilerplate_risk = None

        return QualityReport(
            quality_score=round(confidence, 3),
            feature_scores={},
            missing_signals=gaps,
            staleness_risk=None,
            boilerplate_risk=boilerplate_risk,
            sufficiency_scores=None,
            hard_requirement_coverage=hard_coverage,
            gate_pass=bool(llm_pass) if llm_pass is not None else None,
            reason=reason,
        )

    except Exception as e:
        logger.warning("evidence_quality_llm_failed", error=str(e), query=(query or "")[:80])
        return _build_fail_report(hard_reqs)


def passes_quality_gate(
    report: QualityReport,
    required_evidence: list[str] | None,
    thresholds: dict[str, float] | None = None,
    hard_requirements: list[str] | None = None,
) -> bool:
    """
    PASS behavior (flexible):
    - If gate disabled => True
    - Else:
      - Enforce hard_requirements deterministically using LLM's own coverage booleans.
      - Use LLM's pass/fail as the main verdict.
    """
    settings = get_settings()
    if not getattr(settings, "evidence_quality_enabled", True):
        return True

    hard_reqs = list(dict.fromkeys(hard_requirements or []))
    hard_cov = report.hard_requirement_coverage or {}
    hard_ok = all(hard_cov.get(req) is True for req in hard_reqs) if hard_reqs else True

    # Main verdict from LLM
    if report.gate_pass is not None:
        # When gate_pass=True and no coverage data, trust LLM (backward compat)
        if report.gate_pass and not hard_cov and hard_reqs:
            return True
        return bool(report.gate_pass) and hard_ok

    # gate_pass is None: use hard_coverage as primary fallback (contract-style)
    if hard_reqs and hard_ok:
        return True
    agg_thresh = getattr(settings, "evidence_quality_threshold", 0.6)
    conf_ok = (report.quality_score or 0.0) >= float(agg_thresh)
    return conf_ok and hard_ok