"""Decision Router: deterministic ambiguity/risk routing before generation."""

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk
from app.services.evidence_quality import QualityReport
from app.services.schemas import DecisionResult, QuerySpec

logger = get_logger(__name__)

_DEFAULT_EXACT_ANSWER_TYPES = {"direct_link", "pricing", "policy"}


def _extract_partial_links(evidence: list[EvidenceChunk], max_links: int = 3) -> list[str]:
    """Extract useful URLs from evidence for ASK_USER responses."""
    seen: set[str] = set()
    links: list[str] = []
    for chunk in evidence:
        url = (chunk.source_url or "").strip()
        if not url or url in seen or not url.startswith("http"):
            continue
        seen.add(url)
        links.append(url)
        if len(links) >= max_links:
            break
    return links


def _build_ask_user_missing_constraints(query_spec: QuerySpec) -> str:
    """Human response when constraints are missing."""
    questions = (
        query_spec.blocking_clarifying_questions
        or query_spec.clarifying_questions
        or []
    )
    if questions:
        rendered = "\n".join(f"- {q}" for q in questions[:3])
        return f"We need one detail before answering:\n{rendered}"
    return "We need one detail before answering. Could you specify your product, budget, or region?"


def _build_ask_user_evidence_gap(
    quality_report: QualityReport | None,
    partial_links: list[str],
) -> str:
    """Human response when evidence quality gate failed."""
    if partial_links:
        links = "\n".join(f"- {url}" for url in partial_links[:3])
        return (
            "We couldn't find enough information to answer fully.\n"
            f"You can check these related pages:\n{links}\n"
            "If you want, rephrase your question with the exact detail you need."
        )

    if not (quality_report and quality_report.missing_signals):
        return "We couldn't find enough information to answer fully. Could you rephrase your question?"
    return (
        "We couldn't find enough information to answer fully. "
        "Could you rephrase your question or provide more details?"
    )


def _build_ask_user_ambiguous(query_spec: QuerySpec) -> str:
    """Human response when query is ambiguous."""
    questions = (
        query_spec.blocking_clarifying_questions
        or query_spec.clarifying_questions
        or []
    )
    if questions:
        rendered = "\n".join(f"- {q}" for q in questions[:3])
        return f"We need a bit more clarification before answering:\n{rendered}"
    return "Could you clarify what you need?"


def _get_refinement_questions(query_spec: QuerySpec | None) -> list[str]:
    if not query_spec:
        return []
    questions = (
        query_spec.refinement_questions
        or (
            query_spec.clarifying_questions
            if getattr(query_spec, "answerable_without_clarification", True)
            else []
        )
        or []
    )
    return questions[:1]


def _requires_blocking_clarification(query_spec: QuerySpec | None) -> bool:
    if not query_spec:
        return False
    if getattr(query_spec, "answerable_without_clarification", True) is False:
        return True
    return bool(query_spec.is_ambiguous and not _get_refinement_questions(query_spec))


def _should_use_partial_lane(
    query_spec: QuerySpec | None,
    evidence: list[EvidenceChunk],
    passes_quality_gate: bool,
) -> bool:
    if not query_spec or not evidence:
        return False
    if str(getattr(query_spec, "risk_level", "")).lower() == "high":
        return False
    if getattr(query_spec, "answerable_without_clarification", True) is False:
        return False
    answer_mode = str(getattr(query_spec, "answer_mode", "")).upper()
    if answer_mode == "ASK_USER":
        return False
    if answer_mode == "PASS_PARTIAL":
        return True
    if not passes_quality_gate:
        # Mode-calibration first: with usable evidence in low-risk domains, prefer
        # bounded partial answers over immediate ASK_USER.
        return True
    if getattr(query_spec, "assistant_should_lead", False):
        return True
    if getattr(query_spec, "answer_mode_hint", "") == "weak":
        return True
    if getattr(query_spec, "missing_info_for_refinement", None):
        return True
    return bool(_get_refinement_questions(query_spec))


def _get_answer_type(query_spec: QuerySpec | None) -> str:
    if not query_spec:
        return "general"
    value = str(getattr(query_spec, "answer_type", "")).strip().lower()
    return value or "general"


def _configured_exact_answer_types() -> set[str]:
    raw = getattr(get_settings(), "exact_answer_types", None)
    if isinstance(raw, str):
        configured = [item.strip() for item in raw.split(",") if item.strip()]
    elif isinstance(raw, (list, tuple, set)):
        configured = list(raw)
    else:
        configured = []
    normalized = {
        str(item).strip().lower()
        for item in configured
        if str(item).strip()
    }
    return normalized or set(_DEFAULT_EXACT_ANSWER_TYPES)


def _is_exact_task(query_spec: QuerySpec | None) -> bool:
    return _get_answer_type(query_spec) in _configured_exact_answer_types()


def _should_pass_exact_as_partial(
    query_spec: QuerySpec | None,
    quality_report: QualityReport | None,
    evidence: list[EvidenceChunk],
) -> bool:
    if not query_spec or not evidence:
        return False
    answer_mode = str(getattr(query_spec, "answer_mode", "")).upper()
    if answer_mode == "PASS_PARTIAL":
        return True
    support_level = str(getattr(query_spec, "support_level", "")).strip().lower()
    if support_level == "partial":
        return True
    coverage = getattr(quality_report, "hard_requirement_coverage", None)
    if isinstance(coverage, dict) and any(bool(v) for v in coverage.values()):
        return True
    return bool(_get_refinement_questions(query_spec))


def _build_escalate_response() -> str:
    return "This request requires human review. A support agent will follow up shortly."


def route(
    query_spec: QuerySpec | None,
    quality_report: QualityReport | None,
    evidence: list[EvidenceChunk],
    required_evidence: list[str],
    passes_quality_gate: bool,
) -> DecisionResult:
    """Route to PASS | ASK_USER | ESCALATE using mode-calibration.

    Router scope is intentionally narrow:
    - Ambiguity handling
    - High-risk insufficient-evidence escalation
    - Exact-task routing to candidate verify vs targeted retry
    """
    _ = required_evidence

    if _requires_blocking_clarification(query_spec):
        return DecisionResult(
            decision="ASK_USER",
            reason="ambiguous_query",
            clarifying_questions=(
                query_spec.blocking_clarifying_questions
                or query_spec.clarifying_questions
                or []
            ),
            partial_links=[],
            answer=_build_ask_user_ambiguous(query_spec),
            answer_policy="clarify",
            lane="ASK_USER",
        )

    if query_spec and query_spec.constraints and not query_spec.constraints.get("complete", True):
        return DecisionResult(
            decision="ASK_USER",
            reason="missing_constraints",
            clarifying_questions=(
                query_spec.blocking_clarifying_questions
                or query_spec.clarifying_questions
                or []
            ),
            partial_links=[],
            answer=_build_ask_user_missing_constraints(query_spec),
            answer_policy="clarify",
            lane="ASK_USER",
        )

    if query_spec and query_spec.risk_level == "high" and not passes_quality_gate:
        return DecisionResult(
            decision="ESCALATE",
            reason="high_risk_insufficient",
            clarifying_questions=[],
            partial_links=[],
            answer=_build_escalate_response(),
            answer_policy="human_handoff",
            lane="ESCALATE",
        )

    if _is_exact_task(query_spec):
        if passes_quality_gate and evidence:
            return DecisionResult(
                decision="PASS",
                reason="exact_candidate_verify",
                clarifying_questions=[],
                partial_links=[],
                answer="",
                answer_policy="direct",
                lane="CANDIDATE_VERIFY",
            )
        if _should_pass_exact_as_partial(query_spec, quality_report, evidence):
            return DecisionResult(
                decision="PASS",
                reason="exact_partial_candidate",
                clarifying_questions=_get_refinement_questions(query_spec),
                partial_links=[],
                answer="",
                answer_policy="bounded",
                lane="CANDIDATE_VERIFY",
            )
        if not bool(getattr(get_settings(), "targeted_retry_enabled", True)):
            links = _extract_partial_links(evidence)
            return DecisionResult(
                decision="ASK_USER",
                reason="missing_evidence_quality",
                clarifying_questions=[],
                partial_links=links,
                answer=_build_ask_user_evidence_gap(quality_report, links),
                answer_policy="clarify",
                lane="ASK_USER",
            )
        return DecisionResult(
            decision="PASS",
            reason="exact_targeted_retry",
            clarifying_questions=[],
            partial_links=_extract_partial_links(evidence),
            answer="",
            answer_policy="targeted_retry",
            lane="TARGETED_RETRY",
        )

    if not passes_quality_gate:
        if _should_use_partial_lane(query_spec, evidence, passes_quality_gate):
            return DecisionResult(
                decision="PASS",
                reason="partial_sufficient",
                clarifying_questions=_get_refinement_questions(query_spec),
                partial_links=[],
                answer="",
                answer_policy="bounded",
                lane="CANDIDATE_VERIFY",
            )
        links = _extract_partial_links(evidence)
        return DecisionResult(
            decision="ASK_USER",
            reason="missing_evidence_quality",
            clarifying_questions=[],
            partial_links=links,
            answer=_build_ask_user_evidence_gap(quality_report, links),
            answer_policy="clarify",
            lane="ASK_USER",
        )

    if _should_use_partial_lane(query_spec, evidence, passes_quality_gate):
        return DecisionResult(
            decision="PASS",
            reason="answerable_with_refinement",
            clarifying_questions=_get_refinement_questions(query_spec),
            partial_links=[],
            answer_policy="bounded",
            lane="CANDIDATE_VERIFY",
        )

    return DecisionResult(
        decision="PASS",
        reason="sufficient",
        clarifying_questions=[],
        partial_links=[],
        answer_policy="direct",
        lane="CANDIDATE_VERIFY",
    )


async def route_hybrid(
    query_spec: QuerySpec | None,
    quality_report: QualityReport | None,
    evidence: list[EvidenceChunk],
    required_evidence: list[str],
    passes_quality_gate: bool,
    query: str = "",
) -> DecisionResult:
    """Compatibility wrapper. Hybrid override is disabled in Phase 3."""
    _ = query
    return route(
        query_spec=query_spec,
        quality_report=quality_report,
        evidence=evidence,
        required_evidence=required_evidence,
        passes_quality_gate=passes_quality_gate,
    )
