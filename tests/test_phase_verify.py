"""Tests for verify phase decision propagation."""

import pytest

from app.services.orchestrator import OrchestratorContext
from app.services.phases.verify import execute_verify
from app.services.reviewer import ReviewerResult, ReviewerStatus
from app.services.schemas import DecisionResult


class _ReviewerStub:
    def __init__(self):
        self.last_kwargs = None

    def review(self, **kwargs):
        self.last_kwargs = kwargs
        decision = kwargs.get("decision")
        status = ReviewerStatus.PASS
        if decision == "ASK_USER":
            status = ReviewerStatus.ASK_USER
        elif decision == "ESCALATE":
            status = ReviewerStatus.ESCALATE
        return ReviewerResult(
            status=status,
            reasons=[],
            suggested_queries=[],
            missing_fields=[],
        )


def _ctx() -> OrchestratorContext:
    ctx = OrchestratorContext(query="test query")
    ctx.answer = "answer"
    ctx.citations = []
    ctx.evidence = []
    ctx.confidence = 0.7
    ctx.decision_result = DecisionResult(
        decision="PASS",
        reason="sufficient",
        clarifying_questions=[],
        partial_links=[],
        answer_policy="direct",
        lane="PASS_STRONG",
    )
    return ctx


@pytest.mark.asyncio
async def test_verify_uses_generated_decision_from_context():
    from app.services import phases
    phases.verify._pipeline_log = lambda *args, **kwargs: None

    ctx = _ctx()
    ctx.generated_decision = "ESCALATE"
    reviewer = _ReviewerStub()

    result = await execute_verify(ctx, reviewer=reviewer)

    assert reviewer.last_kwargs is not None
    assert reviewer.last_kwargs["decision"] == "ESCALATE"
    assert result.reviewer_result.status == ReviewerStatus.ESCALATE


@pytest.mark.asyncio
async def test_verify_falls_back_to_pass_for_invalid_generated_decision():
    from app.services import phases
    phases.verify._pipeline_log = lambda *args, **kwargs: None

    ctx = _ctx()
    ctx.generated_decision = "UNEXPECTED"
    reviewer = _ReviewerStub()

    result = await execute_verify(ctx, reviewer=reviewer)

    assert reviewer.last_kwargs is not None
    assert reviewer.last_kwargs["decision"] == "PASS"
    assert result.reviewer_result.status == ReviewerStatus.PASS
