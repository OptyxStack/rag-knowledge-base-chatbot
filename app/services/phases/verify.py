"""VERIFY phase: reviewer gate."""

from app.services.flow_debug import _pipeline_log
from app.services.orchestrator import OrchestratorContext, PhaseResult


def _run_hypothesis_judge(ctx: OrchestratorContext) -> dict | None:
    history = list(ctx.extra.get("hypothesis_history", []))
    if len(history) < 2:
        return None
    ranked = sorted(
        history,
        key=lambda item: (
            1 if item.get("gate_pass") else 0,
            float(item.get("quality_score") or 0.0),
            int(item.get("evidence_count") or 0),
        ),
        reverse=True,
    )
    best = ranked[0]
    return {
        "selected_hypothesis": best.get("name"),
        "selected_retrieval_profile": best.get("retrieval_profile"),
        "selected_evidence_families": best.get("evidence_families", []),
        "attempted_hypotheses": [item.get("name") for item in history],
        "used_multi_hypothesis_judge": True,
    }


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
    hypothesis_judge = _run_hypothesis_judge(ctx)
    _pipeline_log(
        "verify", "done",
        reviewer_input_decision=reviewer_decision,
        reviewer_status=str(status) if status else None,
        selected_hypothesis=(hypothesis_judge or {}).get("selected_hypothesis"),
        trace_id=ctx.trace_id,
    )
    return PhaseResult(
        reviewer_result=reviewer_result,
        hypothesis_judge=hypothesis_judge,
    )
