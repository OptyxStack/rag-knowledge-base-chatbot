"""Offline evaluation harness for replay-style RAG regression testing."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.metrics import (
    offline_eval_answer_correctness,
    offline_eval_cases_total,
    offline_eval_evidence_coverage,
    offline_eval_hallucination_rate,
    offline_eval_retrieval_recall,
    offline_eval_runs_total,
)
from app.services.schemas import AnswerOutput

logger = get_logger(__name__)

_ANSWER_TYPE_ALIASES: dict[str, str] = {
    "link": "direct_link",
    "direct_link": "direct_link",
    "link_lookup": "direct_link",
    "order_link": "direct_link",
    "pricing": "pricing",
    "price_lookup": "pricing",
    "policy": "policy",
    "refund_policy": "policy",
    "troubleshooting": "troubleshooting",
    "setup_steps": "troubleshooting",
    "clarification": "clarification",
    "ask_user": "clarification",
    "general": "general",
    "general_info": "general",
    "escalate": "escalate",
}

_TAG_TO_EXPECTED_ANSWER_TYPE: dict[str, str] = {
    "ambiguous_referent": "clarification",
    "pricing_question": "pricing",
    "policy_question": "policy",
    "troubleshooting_steps": "troubleshooting",
    "direct_link_lookup": "direct_link",
    "link_lookup": "direct_link",
}

_DISCLAIMER_MARKERS = (
    "closest related",
    "closest official",
    "closest",
    "best available information",
    "best we have",
    "best currently available",
    "not confirmed",
    "unverified",
    "could not verify",
    "we don't have that",
    "couldn't find",
    "don't have that",
    "not available in the evidence",
    "based on available information",
    "limited information",
)

_FAQ_LIKE_DOC_TYPES = {"faq", "blog"}

_LINK_HINTS = (
    "/order",
    "/checkout",
    "/cart",
    "/product",
    "/pricing",
    "buy link",
    "order link",
    "purchase link",
    "checkout",
)

_PRICING_HINTS = ("$", "usd", "price", "pricing", "monthly", "/mo", "per month")
_POLICY_HINTS = ("policy", "refund", "terms", "tos", "eligible", "cancellation")
_TROUBLESHOOTING_HINTS = ("step", "run ", "sudo ", "restart", "configure", "reset")


@dataclass
class OfflineEvalCase:
    """One offline eval case loaded from JSONL."""

    name: str
    input: str
    tags: list[str] = field(default_factory=list)
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    expected_decision: str | None = None
    expected_chunk_ids: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    expected_answer_contains: list[str] = field(default_factory=list)
    forbidden_answer_contains: list[str] = field(default_factory=list)
    correctness_threshold: float = 0.7
    hallucination_threshold: float = 0.2
    expected_answer_type: str | None = None
    acceptable_related_types: list[str] = field(default_factory=list)
    expected_answer_mode: str | None = None
    replay_category: str | None = None
    recorded_output: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OfflineEvalCaseResult:
    """Computed metrics for one eval case."""

    run_id: str
    case_name: str
    input: str
    passed: bool
    output_decision: str
    output_confidence: float
    metrics: dict[str, Any]
    output_preview: str
    tags: list[str] = field(default_factory=list)


@dataclass
class OfflineEvalRunSummary:
    """Aggregate metrics for one run."""

    run_id: str
    case_count: int
    pass_count: int
    fail_count: int
    pass_rate: float
    retrieval_recall_avg: float | None
    evidence_coverage_avg: float | None
    answer_correctness_avg: float | None
    hallucination_rate_avg: float | None
    citation_validity_avg: float | None
    wrong_but_cited_rate: float | None = None
    answer_type_mismatch_rate: float | None = None
    partial_without_disclaimer_rate: float | None = None
    faq_returned_for_link_lookup_rate: float | None = None
    category_breakdown: dict[str, Any] = field(default_factory=dict)


def _normalize_answer_type(value: Any) -> str | None:
    text = str(value or "").strip().lower()
    if not text:
        return None
    return _ANSWER_TYPE_ALIASES.get(text, text)


def _bool_rate(values: list[Any]) -> float | None:
    flags = [bool(v) for v in values if isinstance(v, bool)]
    if not flags:
        return None
    return sum(1 for v in flags if v) / len(flags)


def _to_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out = [str(v).strip() for v in value if str(v).strip()]
    else:
        text = str(value).strip()
        out = [text] if text else []
    return list(dict.fromkeys(out))


def _to_history(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role and content:
            out.append({"role": role, "content": content})
    return out


def load_eval_cases_jsonl(path: str | Path) -> list[OfflineEvalCase]:
    """Load offline eval cases from JSONL."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Eval dataset not found: {p}")

    cases: list[OfflineEvalCase] = []
    with p.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {lineno} in {p}: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError(f"Line {lineno} in {p} must be a JSON object")

            raw_input = str(obj.get("input") or obj.get("query") or "").strip()
            name = str(obj.get("name") or f"case_{lineno}").strip()
            if not raw_input:
                raise ValueError(f"Line {lineno} in {p} missing 'input' or 'query'")

            case = OfflineEvalCase(
                name=name,
                input=raw_input,
                tags=_to_str_list(obj.get("tags")),
                conversation_history=_to_history(obj.get("conversation_history")),
                expected_decision=str(obj.get("expected_decision")).strip().upper() or None,
                expected_chunk_ids=_to_str_list(obj.get("expected_chunk_ids")),
                required_evidence=_to_str_list(obj.get("required_evidence")),
                expected_answer_contains=_to_str_list(obj.get("expected_answer_contains")),
                forbidden_answer_contains=_to_str_list(obj.get("forbidden_answer_contains")),
                correctness_threshold=float(obj.get("correctness_threshold", 0.7)),
                hallucination_threshold=float(obj.get("hallucination_threshold", 0.2)),
                expected_answer_type=_normalize_answer_type(
                    obj.get("expected_answer_type")
                    or (obj.get("metadata") or {}).get("expected_answer_type")
                ),
                acceptable_related_types=[
                    t
                    for t in (
                        _normalize_answer_type(v)
                        for v in _to_str_list(
                            obj.get("acceptable_related_types")
                            or (obj.get("metadata") or {}).get("acceptable_related_types")
                        )
                    )
                    if t
                ],
                expected_answer_mode=str(
                    obj.get("expected_answer_mode")
                    or (obj.get("metadata") or {}).get("expected_answer_mode")
                    or ""
                ).strip().lower()
                or None,
                replay_category=str(
                    obj.get("replay_category")
                    or (obj.get("metadata") or {}).get("replay_category")
                    or ""
                ).strip().lower()
                or None,
                recorded_output=(
                    obj.get("recorded_output")
                    if isinstance(obj.get("recorded_output"), dict)
                    else None
                ),
                metadata=obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {},
            )
            cases.append(case)
    return cases


def _extract_debug_evidence_ids(debug: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for row in debug.get("evidence_summary", []) or []:
        if isinstance(row, dict):
            chunk_id = str(row.get("chunk_id", "")).strip()
            if chunk_id:
                ids.add(chunk_id)
    return ids


def _retrieval_recall(case: OfflineEvalCase, debug: dict[str, Any]) -> tuple[float | None, list[str]]:
    expected = {cid for cid in case.expected_chunk_ids if cid}
    if not expected:
        return None, []
    actual = _extract_debug_evidence_ids(debug)
    matched = sorted(expected & actual)
    return len(matched) / max(1, len(expected)), matched


def _evidence_coverage(case: OfflineEvalCase, debug: dict[str, Any]) -> tuple[float | None, list[str]]:
    required = {req for req in case.required_evidence if req}
    if not required:
        return None, []

    covered: set[str] = set()
    evidence_set = debug.get("evidence_set") or {}
    covered_from_set = evidence_set.get("covered_requirements") if isinstance(evidence_set, dict) else []
    for req in _to_str_list(covered_from_set):
        covered.add(req)

    qr = debug.get("quality_report") or {}
    hard_coverage = qr.get("hard_requirement_coverage") if isinstance(qr, dict) else {}
    if isinstance(hard_coverage, dict):
        for req, ok in hard_coverage.items():
            if ok is True:
                covered.add(str(req))

    matched = sorted(required & covered)
    return len(matched) / max(1, len(required)), matched


def _answer_correctness(case: OfflineEvalCase, answer: str) -> tuple[float | None, list[str], list[str]]:
    expected = [s.lower() for s in case.expected_answer_contains if s]
    forbidden = [s.lower() for s in case.forbidden_answer_contains if s]
    if not expected and not forbidden:
        return None, [], []

    answer_l = (answer or "").lower()
    missing_expected = [s for s in expected if s not in answer_l]
    violated_forbidden = [s for s in forbidden if s in answer_l]

    expected_score = 1.0 if not expected else (len(expected) - len(missing_expected)) / len(expected)
    forbidden_penalty = 0.0 if not forbidden else len(violated_forbidden) / len(forbidden)
    score = max(0.0, min(1.0, expected_score - forbidden_penalty))
    return score, missing_expected, violated_forbidden


def _citation_validity(citations: list[dict[str, Any]], debug: dict[str, Any]) -> tuple[float | None, int, int]:
    if not citations:
        return None, 0, 0
    valid_ids = _extract_debug_evidence_ids(debug)
    valid = 0
    total = 0
    for c in citations:
        if not isinstance(c, dict):
            continue
        total += 1
        cid = str(c.get("chunk_id", "")).strip()
        if cid and cid in valid_ids:
            valid += 1
    if total == 0:
        return None, 0, 0
    return valid / total, valid, total


def _hallucination_rate(answer: str, debug: dict[str, Any]) -> tuple[float | None, int]:
    unsupported = debug.get("review_unsupported_claims") or []
    if not isinstance(unsupported, list):
        unsupported = []
    unsupported_count = len(unsupported)
    if unsupported_count == 0:
        return 0.0, 0

    sentences = [s for s in re.split(r"[.!?]+", answer or "") if s.strip()]
    denom = max(1, len(sentences))
    return min(1.0, unsupported_count / denom), unsupported_count


def _extract_debug_doc_types(debug: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in debug.get("evidence_summary", []) or []:
        if not isinstance(row, dict):
            continue
        doc_type = str(row.get("doc_type", "")).strip().lower()
        if doc_type:
            out.append(doc_type)
    return out


def _extract_debug_urls(debug: dict[str, Any]) -> list[str]:
    out: list[str] = []
    for row in debug.get("evidence_summary", []) or []:
        if not isinstance(row, dict):
            continue
        url = str(row.get("source_url", "")).strip().lower()
        if url:
            out.append(url)
    return out


def _extract_citation_doc_types(citations: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for c in citations or []:
        if not isinstance(c, dict):
            continue
        doc_type = str(c.get("doc_type", "")).strip().lower()
        if doc_type:
            out.append(doc_type)
    return out


def _extract_citation_urls(citations: list[dict[str, Any]]) -> list[str]:
    out: list[str] = []
    for c in citations or []:
        if not isinstance(c, dict):
            continue
        url = str(c.get("source_url", "")).strip().lower()
        if url:
            out.append(url)
    return out


def _infer_expected_answer_type(case: OfflineEvalCase) -> str:
    explicit = _normalize_answer_type(case.expected_answer_type)
    if explicit:
        return explicit
    meta_type = _normalize_answer_type(case.metadata.get("expected_answer_type"))
    if meta_type:
        return meta_type
    for tag in case.tags:
        mapped = _TAG_TO_EXPECTED_ANSWER_TYPE.get(str(tag).strip().lower())
        if mapped:
            return mapped
    required = {r.strip().lower() for r in case.required_evidence if r}
    if "transaction_link" in required:
        return "direct_link"
    if "policy_language" in required:
        return "policy"
    if "steps_structure" in required:
        return "troubleshooting"
    if "numbers_units" in required:
        return "pricing"
    if (case.expected_decision or "").upper() == "ASK_USER":
        return "clarification"
    return "general"


def _infer_output_answer_type(
    answer: str,
    decision: str,
    citations: list[dict[str, Any]],
    debug: dict[str, Any],
) -> str:
    d = (decision or "").strip().upper()
    if d == "ASK_USER":
        return "clarification"
    if d == "ESCALATE":
        return "escalate"

    answer_l = (answer or "").lower()
    urls = _extract_citation_urls(citations) + _extract_debug_urls(debug)
    link_signal = any(hint in answer_l for hint in _LINK_HINTS) or any(
        any(hint in url for hint in _LINK_HINTS) for url in urls
    )
    pricing_signal = any(hint in answer_l for hint in _PRICING_HINTS)
    policy_signal = any(hint in answer_l for hint in _POLICY_HINTS)
    troubleshooting_signal = any(hint in answer_l for hint in _TROUBLESHOOTING_HINTS)

    if pricing_signal and not link_signal:
        return "pricing"
    if policy_signal and not troubleshooting_signal:
        return "policy"
    if troubleshooting_signal:
        return "troubleshooting"
    if link_signal:
        return "direct_link"
    if pricing_signal:
        return "pricing"
    if policy_signal:
        return "policy"
    return "general"


def _resolve_acceptable_answer_types(case: OfflineEvalCase, expected_answer_type: str) -> set[str]:
    allowed = {expected_answer_type}
    for item in case.acceptable_related_types:
        normalized = _normalize_answer_type(item)
        if normalized:
            allowed.add(normalized)
    meta_related = _to_str_list(case.metadata.get("acceptable_related_types"))
    for item in meta_related:
        normalized = _normalize_answer_type(item)
        if normalized:
            allowed.add(normalized)
    return allowed


def _is_partial_answer(
    case: OfflineEvalCase,
    answer: str,
    decision: str,
    debug: dict[str, Any],
) -> bool:
    if (decision or "").strip().upper() != "PASS":
        return False
    expected_mode = str(case.expected_answer_mode or "").strip().lower()
    if expected_mode == "partial":
        return True
    decision_router = debug.get("decision_router") or {}
    lane = str(decision_router.get("lane", "")).strip().upper()
    if lane == "PASS_PARTIAL":
        return True
    review_result = debug.get("review_result") or {}
    calibrated_lane = str(review_result.get("final_lane", "")).strip().upper()
    if calibrated_lane == "PASS_PARTIAL":
        return True
    reason = str(decision_router.get("reason", "")).strip().lower()
    if "partial" in reason or "weak" in reason:
        return True
    return bool(_has_partial_disclaimer(answer))


def _has_partial_disclaimer(answer: str) -> bool:
    answer_l = (answer or "").lower()
    return any(marker in answer_l for marker in _DISCLAIMER_MARKERS)


def _faq_returned_for_link_lookup(
    case: OfflineEvalCase,
    citations: list[dict[str, Any]],
    debug: dict[str, Any],
) -> bool:
    expected_answer_type = _infer_expected_answer_type(case)
    required = {r.strip().lower() for r in case.required_evidence if r}
    expects_link = expected_answer_type == "direct_link" or "transaction_link" in required
    if not expects_link:
        return False

    citation_doc_types = _extract_citation_doc_types(citations)
    debug_doc_types = _extract_debug_doc_types(debug)
    doc_types = citation_doc_types or debug_doc_types
    if not doc_types:
        return False
    primary_doc_type = doc_types[0]
    return primary_doc_type in _FAQ_LIKE_DOC_TYPES


def _derive_case_category(case: OfflineEvalCase, expected_answer_type: str) -> str:
    category = str(case.replay_category or case.metadata.get("replay_category") or "").strip().lower()
    if category:
        return category
    for tag in case.tags:
        t = str(tag).strip().lower()
        if t.startswith("category_"):
            return t.replace("category_", "", 1)
    if expected_answer_type == "direct_link":
        return "link"
    if expected_answer_type in {"pricing", "policy", "troubleshooting"}:
        return expected_answer_type
    return "general"


def _build_category_breakdown(results: list[OfflineEvalCaseResult]) -> dict[str, Any]:
    grouped: dict[str, list[OfflineEvalCaseResult]] = {}
    for result in results:
        category = str(result.metrics.get("replay_category") or "uncategorized")
        grouped.setdefault(category, []).append(result)

    breakdown: dict[str, Any] = {}
    for category, rows in sorted(grouped.items()):
        case_count = len(rows)
        pass_count = sum(1 for row in rows if row.passed)
        breakdown[category] = {
            "case_count": case_count,
            "pass_rate": (pass_count / case_count) if case_count else 0.0,
            "wrong_but_cited_rate": _bool_rate([row.metrics.get("wrong_but_cited") for row in rows]),
            "answer_type_mismatch_rate": _bool_rate([row.metrics.get("answer_type_mismatch") for row in rows]),
            "partial_without_disclaimer_rate": _bool_rate(
                [row.metrics.get("partial_without_disclaimer") for row in rows]
            ),
            "faq_returned_for_link_lookup_rate": _bool_rate(
                [row.metrics.get("faq_returned_for_link_lookup") for row in rows]
            ),
        }
    return breakdown


def _to_output_from_recorded(case: OfflineEvalCase) -> AnswerOutput | None:
    raw = case.recorded_output
    if not isinstance(raw, dict):
        return None
    decision = str(raw.get("decision") or case.expected_decision or "PASS").strip().upper()
    if decision not in {"PASS", "ASK_USER", "ESCALATE"}:
        decision = "PASS"
    answer = str(raw.get("answer") or "").strip()
    followup_questions = _to_str_list(raw.get("followup_questions"))
    citations: list[dict[str, str]] = []
    for c in raw.get("citations") or []:
        if not isinstance(c, dict):
            continue
        citations.append(
            {
                "chunk_id": str(c.get("chunk_id", "")).strip(),
                "source_url": str(c.get("source_url", "")).strip(),
                "doc_type": str(c.get("doc_type", "")).strip(),
            }
        )
    try:
        confidence = float(raw.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    debug = raw.get("debug") if isinstance(raw.get("debug"), dict) else {}
    return AnswerOutput(
        decision=decision,
        answer=answer,
        followup_questions=followup_questions,
        citations=citations,
        confidence=confidence,
        debug=debug,
    )


async def evaluate_case(
    answer_service,
    case: OfflineEvalCase,
    run_id: str,
    *,
    use_recorded_output: bool = False,
) -> OfflineEvalCaseResult:
    """Run one case through AnswerService and compute split metrics."""
    trace_id = f"offline-eval-{run_id}-{uuid4().hex[:8]}"
    output = _to_output_from_recorded(case) if use_recorded_output else None
    if output is None:
        if answer_service is None:
            raise ValueError("answer_service is required when recorded output is not available")
        output = await answer_service.generate(
            query=case.input,
            conversation_history=case.conversation_history or None,
            trace_id=trace_id,
        )
    debug = output.debug if isinstance(output.debug, dict) else {}

    retrieval_recall, matched_chunk_ids = _retrieval_recall(case, debug)
    evidence_coverage, matched_requirements = _evidence_coverage(case, debug)
    answer_correctness, missing_expected, forbidden_violations = _answer_correctness(case, output.answer or "")
    citation_validity, citation_valid, citation_total = _citation_validity(output.citations or [], debug)
    hallucination_rate, unsupported_claim_count = _hallucination_rate(output.answer or "", debug)
    expected_answer_type = _infer_expected_answer_type(case)
    output_answer_type = _infer_output_answer_type(
        output.answer or "",
        output.decision,
        output.citations or [],
        debug,
    )
    allowed_answer_types = _resolve_acceptable_answer_types(case, expected_answer_type)
    answer_type_mismatch = output_answer_type not in allowed_answer_types
    partial_answer = _is_partial_answer(case, output.answer or "", output.decision, debug)
    has_partial_disclaimer = _has_partial_disclaimer(output.answer or "")
    partial_without_disclaimer = partial_answer and not has_partial_disclaimer
    faq_for_link_lookup = _faq_returned_for_link_lookup(case, output.citations or [], debug)

    wrong_signals = [
        answer_type_mismatch,
        bool(case.expected_decision and output.decision != case.expected_decision),
        bool(answer_correctness is not None and answer_correctness < case.correctness_threshold),
        bool(forbidden_violations),
    ]
    wrong_but_cited = bool(citation_total > 0 and any(wrong_signals))
    replay_category = _derive_case_category(case, expected_answer_type)

    metrics: dict[str, Any] = {
        "retrieval_recall": retrieval_recall,
        "matched_chunk_ids": matched_chunk_ids,
        "evidence_coverage": evidence_coverage,
        "matched_requirements": matched_requirements,
        "answer_correctness": answer_correctness,
        "missing_expected": missing_expected,
        "forbidden_violations": forbidden_violations,
        "hallucination_rate": hallucination_rate,
        "unsupported_claim_count": unsupported_claim_count,
        "citation_validity": citation_validity,
        "citation_valid_count": citation_valid,
        "citation_total": citation_total,
        "wrong_but_cited": wrong_but_cited,
        "answer_type_expected": expected_answer_type,
        "answer_type_actual": output_answer_type,
        "answer_type_allowed": sorted(allowed_answer_types),
        "answer_type_mismatch": answer_type_mismatch,
        "partial_answer": partial_answer,
        "has_partial_disclaimer": has_partial_disclaimer,
        "partial_without_disclaimer": partial_without_disclaimer,
        "faq_returned_for_link_lookup": faq_for_link_lookup,
        "replay_category": replay_category,
        "decision_match": (
            (output.decision == case.expected_decision) if case.expected_decision else None
        ),
    }

    checks: list[bool] = []
    if case.expected_decision:
        checks.append(output.decision == case.expected_decision)
    if retrieval_recall is not None:
        checks.append(retrieval_recall >= 0.5)
    if evidence_coverage is not None:
        checks.append(evidence_coverage >= 0.5)
    if answer_correctness is not None:
        checks.append(answer_correctness >= case.correctness_threshold)
    if hallucination_rate is not None:
        checks.append(hallucination_rate <= case.hallucination_threshold)
    if citation_validity is not None:
        checks.append(citation_validity >= 0.8)

    passed = all(checks) if checks else True
    outcome = "pass" if passed else "fail"
    offline_eval_cases_total.labels(outcome=outcome).inc()
    if retrieval_recall is not None:
        offline_eval_retrieval_recall.observe(retrieval_recall)
    if evidence_coverage is not None:
        offline_eval_evidence_coverage.observe(evidence_coverage)
    if answer_correctness is not None:
        offline_eval_answer_correctness.observe(answer_correctness)
    if hallucination_rate is not None:
        offline_eval_hallucination_rate.observe(hallucination_rate)

    return OfflineEvalCaseResult(
        run_id=run_id,
        case_name=case.name,
        input=case.input,
        passed=passed,
        output_decision=output.decision,
        output_confidence=float(getattr(output, "confidence", 0.0) or 0.0),
        metrics=metrics,
        output_preview=(output.answer or "")[:400],
        tags=case.tags,
    )


def _avg(values: list[float | None]) -> float | None:
    cleaned = [float(v) for v in values if isinstance(v, (int, float))]
    if not cleaned:
        return None
    return mean(cleaned)


async def run_offline_eval(
    answer_service,
    cases: list[OfflineEvalCase],
    run_id: str | None = None,
    *,
    use_recorded_output: bool = False,
) -> tuple[OfflineEvalRunSummary, list[OfflineEvalCaseResult]]:
    """Execute an offline evaluation run."""
    run_id = run_id or uuid4().hex[:12]
    results: list[OfflineEvalCaseResult] = []
    try:
        for case in cases:
            results.append(
                await evaluate_case(
                    answer_service,
                    case,
                    run_id,
                    use_recorded_output=use_recorded_output,
                )
            )
    except Exception:
        offline_eval_runs_total.labels(status="failed").inc()
        raise

    offline_eval_runs_total.labels(status="success").inc()
    pass_count = sum(1 for r in results if r.passed)
    fail_count = len(results) - pass_count
    summary = OfflineEvalRunSummary(
        run_id=run_id,
        case_count=len(results),
        pass_count=pass_count,
        fail_count=fail_count,
        pass_rate=(pass_count / len(results)) if results else 0.0,
        retrieval_recall_avg=_avg([r.metrics.get("retrieval_recall") for r in results]),
        evidence_coverage_avg=_avg([r.metrics.get("evidence_coverage") for r in results]),
        answer_correctness_avg=_avg([r.metrics.get("answer_correctness") for r in results]),
        hallucination_rate_avg=_avg([r.metrics.get("hallucination_rate") for r in results]),
        citation_validity_avg=_avg([r.metrics.get("citation_validity") for r in results]),
        wrong_but_cited_rate=_bool_rate([r.metrics.get("wrong_but_cited") for r in results]),
        answer_type_mismatch_rate=_bool_rate([r.metrics.get("answer_type_mismatch") for r in results]),
        partial_without_disclaimer_rate=_bool_rate(
            [r.metrics.get("partial_without_disclaimer") for r in results]
        ),
        faq_returned_for_link_lookup_rate=_bool_rate(
            [r.metrics.get("faq_returned_for_link_lookup") for r in results]
        ),
        category_breakdown=_build_category_breakdown(results),
    )
    return summary, results


async def persist_eval_run(cases: list[OfflineEvalCase], results: list[OfflineEvalCaseResult]) -> None:
    """Persist cases/results into eval_cases + eval_results tables."""
    if not cases or not results:
        return
    from app.db.models import EvalCase, EvalResult
    from app.db.session import db_session

    by_name: dict[str, OfflineEvalCase] = {case.name: case for case in cases}

    async with db_session() as session:
        for result in results:
            case = by_name.get(result.case_name)
            if case is None:
                continue

            existing_q = await session.execute(
                select(EvalCase).where(
                    EvalCase.name == case.name,
                    EvalCase.input == case.input,
                )
            )
            db_case = existing_q.scalars().first()
            expected_tags = {
                "tags": case.tags,
                "required_evidence": case.required_evidence,
                "expected_decision": case.expected_decision,
                "metadata": case.metadata,
            }

            if db_case is None:
                db_case = EvalCase(
                    name=case.name,
                    input=case.input,
                    expected_policy_tags=expected_tags,
                )
                session.add(db_case)
                await session.flush()
            else:
                db_case.expected_policy_tags = expected_tags
                await session.flush()

            session.add(
                EvalResult(
                    eval_case_id=db_case.id,
                    run_id=result.run_id,
                    pass_=result.passed,
                    metrics=result.metrics,
                )
            )
        await session.flush()


def build_eval_dashboard(
    summary: OfflineEvalRunSummary,
    results: list[OfflineEvalCaseResult],
) -> dict[str, Any]:
    """Build a compact dashboard payload for baseline tracking."""
    metric_failures = {
        "wrong_but_cited": [],
        "answer_type_mismatch": [],
        "partial_without_disclaimer": [],
        "faq_returned_for_link_lookup": [],
    }
    for result in results:
        for key in metric_failures:
            if result.metrics.get(key) is True:
                metric_failures[key].append(result.case_name)

    return {
        "run_id": summary.run_id,
        "totals": {
            "case_count": summary.case_count,
            "pass_count": summary.pass_count,
            "fail_count": summary.fail_count,
            "pass_rate": summary.pass_rate,
        },
        "phase0_metrics": {
            "wrong_but_cited_rate": summary.wrong_but_cited_rate,
            "answer_type_mismatch_rate": summary.answer_type_mismatch_rate,
            "partial_without_disclaimer_rate": summary.partial_without_disclaimer_rate,
            "faq_returned_for_link_lookup_rate": summary.faq_returned_for_link_lookup_rate,
        },
        "category_breakdown": summary.category_breakdown,
        "metric_failures": metric_failures,
    }


def render_eval_dashboard_markdown(
    summary: OfflineEvalRunSummary,
    results: list[OfflineEvalCaseResult],
) -> str:
    """Render a lightweight markdown dashboard for quick baseline review."""
    dashboard = build_eval_dashboard(summary, results)
    lines: list[str] = [
        f"# Offline Eval Baseline ({summary.run_id})",
        "",
        f"- Cases: {summary.case_count}",
        f"- Pass rate: {summary.pass_rate:.2%}",
        f"- wrong_but_cited_rate: {(summary.wrong_but_cited_rate or 0.0):.2%}",
        f"- answer_type_mismatch_rate: {(summary.answer_type_mismatch_rate or 0.0):.2%}",
        f"- partial_without_disclaimer_rate: {(summary.partial_without_disclaimer_rate or 0.0):.2%}",
        f"- faq_returned_for_link_lookup_rate: {(summary.faq_returned_for_link_lookup_rate or 0.0):.2%}",
        "",
        "## By category",
    ]

    for category, row in sorted((dashboard.get("category_breakdown") or {}).items()):
        lines.append(
            f"- {category}: cases={row.get('case_count', 0)}, "
            f"pass_rate={float(row.get('pass_rate', 0.0)):.2%}, "
            f"wrong_but_cited={float(row.get('wrong_but_cited_rate') or 0.0):.2%}, "
            f"answer_type_mismatch={float(row.get('answer_type_mismatch_rate') or 0.0):.2%}, "
            f"partial_without_disclaimer={float(row.get('partial_without_disclaimer_rate') or 0.0):.2%}, "
            f"faq_for_link_lookup={float(row.get('faq_returned_for_link_lookup_rate') or 0.0):.2%}"
        )

    lines.append("")
    lines.append("## Failure buckets")
    metric_failures = dashboard.get("metric_failures") or {}
    for key in (
        "wrong_but_cited",
        "answer_type_mismatch",
        "partial_without_disclaimer",
        "faq_returned_for_link_lookup",
    ):
        names = metric_failures.get(key) or []
        if not names:
            lines.append(f"- {key}: 0")
            continue
        preview = ", ".join(names[:10])
        suffix = " ..." if len(names) > 10 else ""
        lines.append(f"- {key}: {len(names)} ({preview}{suffix})")

    return "\n".join(lines).strip() + "\n"


def dump_eval_run_markdown(
    path: str | Path,
    summary: OfflineEvalRunSummary,
    results: list[OfflineEvalCaseResult],
) -> None:
    """Write markdown baseline dashboard."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(render_eval_dashboard_markdown(summary, results), encoding="utf-8")
    logger.info("offline_eval_markdown_written", path=str(p), cases=len(results))


def dump_eval_run_json(
    path: str | Path,
    summary: OfflineEvalRunSummary,
    results: list[OfflineEvalCaseResult],
) -> None:
    """Write eval summary + case results to JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": asdict(summary),
        "results": [asdict(r) for r in results],
        "dashboard": build_eval_dashboard(summary, results),
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("offline_eval_written", path=str(p), cases=len(results))
