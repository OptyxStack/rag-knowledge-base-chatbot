"""Evidence Quality Gate - flexible, LLM-led."""

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

    quality_score: float
    feature_scores: dict[str, float]
    missing_signals: list[str]
    staleness_risk: float | None
    boilerplate_risk: float | None
    sufficiency_scores: dict[str, float] | None = None
    hard_requirement_coverage: dict[str, bool] | None = None
    completeness_score: float | None = None
    actionability_score: float | None = None
    gate_pass: bool | None = None
    reason: str | None = None


EVIDENCE_QUALITY_PROMPT = """You judge whether provided evidence is sufficient for a support answer.

Output MUST be exactly this JSON object. No markdown, no code fences, no extra text.
{
  "is_sufficient": true,
  "confidence": 0.8,
  "completeness": 0.8,
  "actionability": 0.8,
  "reason": "Brief reason.",
  "gaps": [],
  "coverage": {}
}

Field rules (strict):
- is_sufficient: boolean true or false only. true means evidence supports a useful and actionable answer now.
- confidence: number 0.0 to 1.0
- completeness: number 0.0 to 1.0. 1.0 means evidence covers major options/details needed by the query.
- actionability: number 0.0 to 1.0. 1.0 means user can act now (clear steps/options/links/policies).
- reason: one short sentence
- gaps: array of strings, empty when is_sufficient=true
- coverage: object mapping requirement names to boolean. Use hint.required_evidence keys when available.

Judgment guidance:
- Evaluate sufficiency in context, not by keyword overlap. Ask: is this evidence sufficient for a complete, actionable answer?
- If query expects comparison/recommendation/procedural guidance, check whether evidence covers major options or explicitly states limits.
- Prefer structured documentation for decisive coverage. Conversation can support but should not be treated as complete when structured docs indicate broader options.
- Set false when evidence is vague, contradictory, not actionable, or too incomplete for a practical answer.

Actionability: evidence is actionable when the user can act now - clear steps, links, options, or policy clauses. Incomplete when only one path is shown but structured docs indicate multiple valid paths.

Coverage guidance:
- If policy evidence states exclusions, treat promo/discount/special-offer wording as synonymous for coverage.
- For policy/refund questions: if policy_language evidence directly answers the query, mark sufficient and set coverage accordingly.
"""


def _build_fail_report(hard_requirements: list[str] | None) -> QualityReport:
    hard_reqs = list(dict.fromkeys(hard_requirements or []))
    hard_coverage = {req: False for req in hard_reqs}
    return QualityReport(
        quality_score=0.0,
        feature_scores={},
        missing_signals=["missing_evidence"],
        staleness_risk=None,
        boilerplate_risk=None,
        sufficiency_scores=None,
        hard_requirement_coverage=hard_coverage,
        completeness_score=0.0,
        actionability_score=0.0,
        gate_pass=False,
        reason="No evidence chunks provided.",
    )


def _extract_probable_json(text: str) -> str:
    s = (text or "").strip()

    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        last = s.rfind("```")
        if last != -1:
            s = s[:last].strip()

    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        return s

    start = s.find("{")
    end = s.rfind("}")
    if 0 <= start < end:
        return s[start : end + 1].strip()

    return s


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
    conversation_history: list[dict[str, str]] | None = None,
    context: dict[str, Any] | None = None,
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

    if conversation_history:
        from app.services.conversation_context import truncate_for_prompt

        truncated = truncate_for_prompt(conversation_history)
        ctx_block = "\n".join(
            f"{m.get('role', 'user')}: {(m.get('content') or '')[:400]}"
            for m in truncated
        )
        user_content += f"\n\nConversation context (last {len(truncated)} messages):\n{ctx_block}"

    hint: dict[str, Any] = {}
    if reqs:
        hint["required_evidence"] = reqs
    if hard_reqs:
        hint["hard_requirements"] = hard_reqs
    if product_type:
        hint["product_type"] = product_type

    doc_type_counts: dict[str, int] = {}
    for c in chunks:
        dt = (c.doc_type or "unknown").strip().lower() or "unknown"
        doc_type_counts[dt] = doc_type_counts.get(dt, 0) + 1
    hint["evidence_doc_type_counts"] = doc_type_counts

    if hint:
        user_content += "\n\nHint (query context): " + json.dumps(hint, ensure_ascii=False)
    if context:
        user_content += "\n\nAssessment context: " + json.dumps(context, ensure_ascii=False)

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
            max_tokens=320,
        )

        raw = (resp.content or "").strip()
        text = _extract_probable_json(raw)
        data = json.loads(text)

        logger.debug(
            "evidence_quality_llm_raw",
            raw_preview=raw[:500] if raw else "",
            parsed=data,
            query_preview=(query or "")[:60],
        )

        llm_pass = _coerce_bool(data.get("is_sufficient", data.get("pass")))
        confidence = _coerce_float(data.get("confidence"), default=0.5)
        completeness = _coerce_float(data.get("completeness"), default=confidence)
        actionability = _coerce_float(data.get("actionability"), default=confidence)
        reason = str(data.get("reason") or "").strip() or None

        gaps = _to_str_list(data.get("gaps") or data.get("missing_signals"))
        coverage_raw = data.get("coverage") or {}
        if not isinstance(coverage_raw, dict):
            coverage_raw = {}

        coverage: dict[str, bool] = {}
        for k, v in coverage_raw.items():
            if isinstance(k, str) and isinstance(v, bool):
                coverage[k] = v

        hard_coverage = {req: bool(coverage.get(req, False)) for req in hard_reqs}

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
            completeness_score=round(completeness, 3),
            actionability_score=round(actionability, 3),
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
    PASS behavior:
    - If gate disabled => True
    - Else enforce hard requirements from LLM coverage and use LLM pass/fail as primary
    """
    _ = required_evidence
    _ = thresholds

    settings = get_settings()
    if not getattr(settings, "evidence_quality_enabled", True):
        return True

    hard_reqs = list(dict.fromkeys(hard_requirements or []))
    hard_cov = report.hard_requirement_coverage or {}
    hard_ok = all(hard_cov.get(req) is True for req in hard_reqs) if hard_reqs else True

    if report.gate_pass is not None:
        if report.gate_pass and not hard_cov and hard_reqs:
            return True
        return bool(report.gate_pass) and hard_ok

    if hard_reqs and hard_ok:
        return True

    agg_thresh = getattr(settings, "evidence_quality_threshold", 0.6)
    conf_ok = (report.quality_score or 0.0) >= float(agg_thresh)
    min_completeness = float(getattr(settings, "evidence_quality_min_completeness", 0.35) or 0.35)
    completeness_ok = (
        report.completeness_score is None
        or float(report.completeness_score) >= min_completeness
    )
    return conf_ok and hard_ok and completeness_ok

