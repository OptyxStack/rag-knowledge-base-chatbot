"""Decision Router: deterministic ambiguity/risk routing before generation."""

from app.core.logging import get_logger
from app.search.base import EvidenceChunk
from app.services.evidence_quality import QualityReport
from app.services.schemas import DecisionResult, QuerySpec

logger = get_logger(__name__)


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
    questions = query_spec.clarifying_questions or []
    if questions:
        rendered = "\n".join(f"- {q}" for q in questions[:3])
        return f"I need one detail before answering:\n{rendered}"
    return "I need one detail before answering. Could you specify your product, budget, or region?"


def _build_ask_user_evidence_gap(
    quality_report: QualityReport | None,
    partial_links: list[str],
) -> str:
    """Human response when evidence quality gate failed."""
    if partial_links:
        links = "\n".join(f"- {url}" for url in partial_links[:3])
        return (
            "I couldn't verify enough details from the current evidence.\n"
            f"You can check these related pages:\n{links}\n"
            "If you want, rephrase your question with the exact detail you need."
        )

    missing = list((quality_report.missing_signals if quality_report else []) or [])
    if not missing:
        return "I couldn't verify enough details from the current evidence. Could you rephrase your question?"
    return (
        "I couldn't verify enough details from the current evidence. "
        f"Missing signals: {', '.join(missing[:3])}. "
        "Could you rephrase your question?"
    )


def _build_ask_user_ambiguous(query_spec: QuerySpec) -> str:
    """Human response when query is ambiguous."""
    questions = query_spec.clarifying_questions or []
    if questions:
        rendered = "\n".join(f"- {q}" for q in questions[:3])
        return f"I need clarification before answering:\n{rendered}"
    return "Could you clarify what you need?"


def _build_escalate_response() -> str:
    return "This request requires human review. A support agent will follow up shortly."


def route(
    query_spec: QuerySpec | None,
    quality_report: QualityReport | None,
    evidence: list[EvidenceChunk],
    required_evidence: list[str],
    passes_quality_gate: bool,
) -> DecisionResult:
    """Route to PASS | ASK_USER | ESCALATE after quality gate.

    Router scope is intentionally narrow:
    - Ambiguity handling
    - High-risk insufficient-evidence escalation
    - Evidence-gate failure deferral
    """
    _ = required_evidence

    if query_spec and query_spec.is_ambiguous:
        return DecisionResult(
            decision="ASK_USER",
            reason="ambiguous_query",
            clarifying_questions=query_spec.clarifying_questions,
            partial_links=[],
            answer=_build_ask_user_ambiguous(query_spec),
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

    if not passes_quality_gate:
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

    if query_spec and query_spec.constraints and not query_spec.constraints.get("complete", True):
        return DecisionResult(
            decision="ASK_USER",
            reason="missing_constraints",
            clarifying_questions=query_spec.clarifying_questions,
            partial_links=[],
            answer=_build_ask_user_missing_constraints(query_spec),
            answer_policy="clarify",
            lane="ASK_USER",
        )

    return DecisionResult(
        decision="PASS",
        reason="sufficient",
        clarifying_questions=[],
        partial_links=[],
        answer_policy="direct",
        lane="PASS_STRONG",
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
