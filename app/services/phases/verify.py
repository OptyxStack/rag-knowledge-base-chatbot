"""VERIFY phase: reviewer gate."""

from app.services.flow_debug import _pipeline_log
from app.services.orchestrator import OrchestratorContext, PhaseResult


async def execute_verify(ctx: OrchestratorContext, *, reviewer) -> PhaseResult:
    """Run reviewer gate on generated answer."""
    dr = ctx.decision_result
    reviewer_decision = (ctx.generated_decision or ctx.extra.get("generated_decision") or "PASS").upper()
    if reviewer_decision not in {"PASS", "ASK_USER", "ESCALATE"}:
        reviewer_decision = "PASS"
    reviewer_result = reviewer.review(
        decision=reviewer_decision,
        answer=ctx.answer,
        citations=ctx.citations,
        evidence=ctx.evidence,
        query=ctx.query,
        confidence=ctx.confidence,
        retrieval_attempt=ctx.retrieval_attempt + 1,
        max_attempts=ctx.max_attempts,
        answer_policy=dr.answer_policy if dr else "direct",
        lane=dr.resolved_lane() if dr else None,
    )
    status = getattr(reviewer_result, "status", None)
    _pipeline_log(
        "verify", "done",
        reviewer_input_decision=reviewer_decision,
        reviewer_status=str(status) if status else None,
        trace_id=ctx.trace_id,
    )
    return PhaseResult(reviewer_result=reviewer_result)
