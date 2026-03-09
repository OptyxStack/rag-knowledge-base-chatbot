"""Tests for Orchestrator state machine per RAG_DEVELOPMENT_STRATEGY."""

import pytest

from app.services.orchestrator import (
    Orchestrator,
    OrchestratorAction,
    OrchestratorContext,
    OrchestratorState,
    PhaseResult,
)
from app.services.schemas import DecisionResult, QuerySpec


def _ctx(
    state: OrchestratorState = OrchestratorState.INIT,
    query: str = "test",
    max_attempts: int = 2,
    **kwargs,
) -> OrchestratorContext:
    return OrchestratorContext(query=query, state=state, max_attempts=max_attempts, **kwargs)


def test_next_action_init_returns_understand():
    orch = Orchestrator()
    ctx = _ctx(OrchestratorState.INIT)
    assert orch.next_action(ctx) == OrchestratorAction.UNDERSTAND


def test_next_action_understanding_returns_retrieve():
    orch = Orchestrator()
    ctx = _ctx(OrchestratorState.UNDERSTANDING)
    assert orch.next_action(ctx) == OrchestratorAction.RETRIEVE


def test_next_action_retrieving_with_evidence_returns_assess():
    orch = Orchestrator()
    ctx = _ctx(OrchestratorState.RETRIEVING, evidence=[{"id": "1"}])
    assert orch.next_action(ctx, has_evidence=True) == OrchestratorAction.ASSESS_EVIDENCE


def test_next_action_retrieving_no_evidence_can_retry_returns_retry():
    orch = Orchestrator()
    ctx = _ctx(OrchestratorState.RETRIEVING, retrieval_attempt=0, max_attempts=2)
    assert orch.next_action(ctx, has_evidence=False) == OrchestratorAction.RETRY_RETRIEVE


def test_next_action_retrieving_no_evidence_no_retry_returns_ask_user():
    orch = Orchestrator()
    ctx = _ctx(OrchestratorState.RETRIEVING, retrieval_attempt=2, max_attempts=2)
    assert orch.next_action(ctx, has_evidence=False) == OrchestratorAction.ASK_USER


def test_next_action_assessing_quality_fail_can_retry_returns_retry():
    orch = Orchestrator()
    ctx = _ctx(
        OrchestratorState.ASSESSING,
        passes_quality_gate=False,
        retrieval_attempt=0,
        max_attempts=2,
    )
    assert orch.next_action(ctx) == OrchestratorAction.RETRY_RETRIEVE


def test_next_action_assessing_quality_fail_no_retry_returns_decide():
    orch = Orchestrator()
    ctx = _ctx(
        OrchestratorState.ASSESSING,
        passes_quality_gate=False,
        retrieval_attempt=2,
        max_attempts=2,
    )
    assert orch.next_action(ctx) == OrchestratorAction.DECIDE


def test_next_action_deciding_pass_returns_generate():
    orch = Orchestrator()
    ctx = _ctx(OrchestratorState.DECIDING)
    ctx.decision_result = DecisionResult(
        decision="PASS",
        reason="sufficient",
        clarifying_questions=[],
        partial_links=[],
        lane="PASS_STRONG",
    )
    assert orch.next_action(ctx) == OrchestratorAction.GENERATE


def test_next_action_deciding_escalate_returns_escalate():
    orch = Orchestrator()
    ctx = _ctx(OrchestratorState.DECIDING)
    ctx.decision_result = DecisionResult(
        decision="ESCALATE",
        reason="high_risk",
        clarifying_questions=[],
        partial_links=[],
        lane="ESCALATE",
    )
    assert orch.next_action(ctx) == OrchestratorAction.ESCALATE


def test_next_action_deciding_ask_user_returns_ask_user():
    """ASK_USER from decision router is terminal."""
    orch = Orchestrator()
    ctx = _ctx(
        OrchestratorState.DECIDING,
        retrieval_attempt=0,
        max_attempts=2,
    )
    ctx.decision_result = DecisionResult(
        decision="ASK_USER",
        reason="missing_evidence",
        clarifying_questions=[],
        partial_links=[],
        lane="ASK_USER",
    )
    assert orch.next_action(ctx) == OrchestratorAction.ASK_USER


def test_next_action_reviewing_pass_returns_done():
    orch = Orchestrator()
    ctx = _ctx(OrchestratorState.REVIEWING)
    ctx._last_reviewer_result = type("R", (), {"status": type("S", (), {"value": "PASS"})()})()
    assert orch.next_action(ctx, reviewer_status="PASS") == OrchestratorAction.DONE


def test_next_action_reviewing_retrieve_more_defaults_to_ask_user():
    orch = Orchestrator()
    ctx = _ctx(
        OrchestratorState.REVIEWING,
        retrieval_attempt=0,
        max_attempts=2,
    )
    assert orch.next_action(ctx, reviewer_status="RETRIEVE_MORE") == OrchestratorAction.ASK_USER


def test_next_action_retrying_returns_retrieve():
    orch = Orchestrator()
    ctx = _ctx(OrchestratorState.RETRYING)
    assert orch.next_action(ctx) == OrchestratorAction.RETRIEVE


def test_context_add_stage_reason():
    ctx = _ctx()
    ctx.add_stage_reason("understand", "query_spec_ready")
    ctx.add_stage_reason("retrieve", "chunks=5")
    assert len(ctx.stage_reasons) == 2
    assert "understand: query_spec_ready" in ctx.stage_reasons[0]
    assert "retrieve: chunks=5" in ctx.stage_reasons[1]


def test_context_can_retry():
    ctx = _ctx(retrieval_attempt=0, max_attempts=2)
    assert ctx.can_retry() is True
    ctx.retrieval_attempt = 1
    assert ctx.can_retry() is True
    ctx.retrieval_attempt = 2
    assert ctx.can_retry() is False


def test_context_current_lane_from_decision_result():
    ctx = _ctx()
    ctx.decision_result = DecisionResult(
        decision="PASS",
        reason="sufficient",
        clarifying_questions=[],
        partial_links=[],
        lane="PASS_WEAK",
    )
    assert ctx.current_lane() == "PASS_WEAK"


@pytest.mark.asyncio
async def test_run_terminates_on_ask_user():
    """Run terminates when handler returns terminal output for ASK_USER."""
    class MockHandlers:
        async def execute(self, ctx, action):
            if action == OrchestratorAction.UNDERSTAND:
                return PhaseResult(
                    query_spec=QuerySpec(
                        intent="informational",
                        entities=[],
                        constraints={},
                        required_evidence=[],
                        risk_level="low",
                        keyword_queries=[],
                        semantic_queries=[],
                        clarifying_questions=[],
                    ),
                    effective_query="test",
                )
            if action == OrchestratorAction.RETRIEVE:
                return PhaseResult(evidence=[], evidence_pack=None)
            raise NotImplementedError(action)

        async def build_output(self, ctx, action):
            return {"decision": action.value, "answer": ""}

    orch = Orchestrator()
    ctx = OrchestratorContext(query="test", max_attempts=1)
    handlers = MockHandlers()
    out = await orch.run(ctx, handlers)
    assert out["decision"] == "ask_user"
