"""Orchestrator: workflow state machine, model routing, retry lifecycle.

Drives the RAG pipeline per RAG_DEVELOPMENT_STRATEGY Workstream 1:
- Runtime context management
- State transitions
- Decision reason logging

The orchestrator is the source of truth for flow control. Handlers execute
each phase and return results; the orchestrator updates context and determines
the next action.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from app.core.logging import get_logger
from app.services.llm_config import get_llm_fallback_model, get_llm_model
from app.services.model_router import get_model_for_task
from app.services.schemas import (
    AnswerPlan,
    CandidatePool,
    DecisionResult,
    EvidenceAssessment,
    EvidenceSet,
    QuerySpec,
    RetrievalPlan,
    ReviewResult,
)

logger = get_logger(__name__)


class OrchestratorAction(str, Enum):
    """Next action in workflow."""

    UNDERSTAND = "understand"
    RETRIEVE = "retrieve"
    ASSESS_EVIDENCE = "assess_evidence"
    DECIDE = "decide"
    GENERATE = "generate"
    VERIFY = "verify"
    ASK_USER = "ask_user"
    ESCALATE = "escalate"
    RETRY_RETRIEVE = "retry_retrieve"
    DONE = "done"


class OrchestratorState(str, Enum):
    """Workflow state."""

    INIT = "init"
    UNDERSTANDING = "understanding"
    RETRIEVING = "retrieving"
    ASSESSING = "assessing"
    DECIDING = "deciding"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    RETRYING = "retrying"
    COMPLETE = "complete"


@dataclass
class PhaseResult:
    """Result from executing one phase. Fields used depend on the action."""

    query_spec: QuerySpec | None = None
    effective_query: str = ""
    source_lang: str = "en"
    skip_retrieval: bool = False
    canned_response: str | None = None
    evidence_pack: Any = None
    evidence: list[Any] = field(default_factory=list)
    quality_report: Any = None
    passes_quality_gate: bool = False
    decision_result: DecisionResult | None = None
    answer_plan: AnswerPlan | None = None
    answer: str = ""
    citations: list[Any] = field(default_factory=list)
    followup: list[str] = field(default_factory=list)
    confidence: float = 0.0
    generated_decision: str | None = None
    reviewer_result: Any = None
    retry_query_override: str | None = None


@dataclass
class OrchestratorContext:
    """Context passed through workflow. Single source of truth for pipeline state."""

    query: str
    state: OrchestratorState = OrchestratorState.INIT
    trace_id: str | None = None
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    attempt: int = 1
    max_attempts: int = 2
    evidence: list[Any] = field(default_factory=list)
    query_spec: QuerySpec | None = None
    effective_query: str = ""
    source_lang: str = "en"
    retrieval_plan: RetrievalPlan | None = None
    candidate_pool: CandidatePool | None = None
    evidence_set: EvidenceSet | None = None
    evidence_assessment: EvidenceAssessment | None = None
    evidence_pack: Any = None
    quality_report: Any = None
    passes_quality_gate: bool = False
    decision_result: DecisionResult | None = None
    answer_lane: str | None = None
    answer_plan: AnswerPlan | None = None
    review_result: ReviewResult | None = None
    model_override: str | None = None
    retrieval_attempt: int = 0
    stage_reasons: list[str] = field(default_factory=list)
    retrieval_history: list[RetrievalPlan] = field(default_factory=list)
    termination_reason: str | None = None
    answer: str = ""
    citations: list[Any] = field(default_factory=list)
    followup: list[str] = field(default_factory=list)
    confidence: float = 0.0
    generated_decision: str | None = None
    retry_query_override: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def can_retry(self) -> bool:
        """Whether another retrieval attempt is still allowed."""
        return self.retrieval_attempt < self.max_attempts

    def current_lane(self) -> str | None:
        """Return the best-known answer lane for this context."""
        if self.review_result:
            return self.review_result.final_lane
        if self.decision_result:
            return self.decision_result.resolved_lane()
        return self.answer_lane

    def add_stage_reason(self, stage: str, reason: str) -> None:
        """Append decision reason for explainability (strategy: decision reason logging)."""
        self.stage_reasons.append(f"{stage}: {reason}")


@runtime_checkable
class OrchestratorHandlers(Protocol):
    """Protocol for phase execution. Implemented by AnswerService or test doubles."""

    async def execute(self, ctx: OrchestratorContext, action: OrchestratorAction) -> PhaseResult:
        """Execute the given action and return phase result."""
        ...

    async def build_output(self, ctx: OrchestratorContext, action: OrchestratorAction) -> Any:
        """Build final output for terminal actions (DONE, ASK_USER, ESCALATE)."""
        ...


def _reviewer_status_to_str(reviewer_result: Any) -> str | None:
    """Map ReviewerResult.status to string for next_action."""
    if reviewer_result is None:
        return None
    status = getattr(reviewer_result, "status", None)
    if status is None:
        return None
    return str(status.value) if hasattr(status, "value") else str(status)


class Orchestrator:
    """State machine orchestrator for support flow. Drives pipeline per strategy."""

    def __init__(
        self,
        primary_model: str | None = None,
        fallback_model: str | None = None,
    ):
        self.primary_model = primary_model or get_llm_model()
        self.fallback_model = fallback_model or get_llm_fallback_model()
        self.models = [self.primary_model, self.fallback_model]

    def get_model_for_query(self, query: str) -> str:
        """Model for generate phase (primary/gpt-5.2)."""
        return get_model_for_task("generate")

    def get_model_for_task(self, task: str, query: str = "") -> str:
        """Task-aware routing: primary for generate/self_critic, economy for rest."""
        _ = query
        return get_model_for_task(task)

    def next_action(
        self,
        ctx: OrchestratorContext,
        reviewer_status: str | None = None,
        has_evidence: bool = False,
    ) -> OrchestratorAction:
        """Determine next action from current state and reviewer result."""
        if ctx.state == OrchestratorState.INIT:
            return OrchestratorAction.UNDERSTAND

        if ctx.state == OrchestratorState.UNDERSTANDING:
            return OrchestratorAction.RETRIEVE

        if ctx.state == OrchestratorState.RETRIEVING:
            if has_evidence:
                return OrchestratorAction.ASSESS_EVIDENCE
            if ctx.can_retry():
                return OrchestratorAction.RETRY_RETRIEVE
            return OrchestratorAction.ASK_USER

        if ctx.state == OrchestratorState.ASSESSING:
            if not ctx.passes_quality_gate and ctx.can_retry():
                return OrchestratorAction.RETRY_RETRIEVE
            return OrchestratorAction.DECIDE

        if ctx.state == OrchestratorState.DECIDING:
            lane = ctx.current_lane()
            if lane in ("PASS_STRONG", "PASS_WEAK", "PASS"):
                return OrchestratorAction.GENERATE
            if lane == "ESCALATE":
                return OrchestratorAction.ESCALATE
            if lane == "ASK_USER":
                return OrchestratorAction.ASK_USER
            if ctx.can_retry():
                return OrchestratorAction.RETRY_RETRIEVE
            return OrchestratorAction.ASK_USER

        if ctx.state == OrchestratorState.GENERATING:
            return OrchestratorAction.VERIFY

        if ctx.state == OrchestratorState.REVIEWING:
            if reviewer_status == "PASS":
                return OrchestratorAction.DONE
            if reviewer_status in ("TRIM_UNSUPPORTED", "DOWNGRADE_LANE"):
                return OrchestratorAction.DONE
            if reviewer_status == "ESCALATE":
                return OrchestratorAction.ESCALATE
            if reviewer_status == "ASK_USER":
                return OrchestratorAction.ASK_USER
            return OrchestratorAction.ASK_USER

        if ctx.state == OrchestratorState.RETRYING:
            return OrchestratorAction.RETRIEVE

        return OrchestratorAction.DONE

    def _apply_result(
        self,
        ctx: OrchestratorContext,
        action: OrchestratorAction,
        result: PhaseResult,
    ) -> None:
        """Update context after executing an action. Handles state transitions."""
        if action == OrchestratorAction.UNDERSTAND:
            if result.query_spec:
                ctx.query_spec = result.query_spec
            ctx.effective_query = result.effective_query or ctx.query
            ctx.source_lang = result.source_lang or "en"
            ctx.state = OrchestratorState.UNDERSTANDING
            ctx.add_stage_reason("understand", "query_spec_ready")

        elif action == OrchestratorAction.RETRIEVE:
            ctx.evidence_pack = result.evidence_pack
            ctx.evidence = result.evidence
            if result.evidence_pack:
                ctx.retrieval_plan = getattr(result.evidence_pack, "retrieval_plan", None)
                ctx.candidate_pool = getattr(result.evidence_pack, "candidate_pool", None)
                ctx.evidence_set = getattr(result.evidence_pack, "evidence_set", None)
            ctx.state = OrchestratorState.RETRIEVING
            ctx.add_stage_reason("retrieve", f"chunks={len(result.evidence)}")

        elif action == OrchestratorAction.ASSESS_EVIDENCE:
            ctx.quality_report = result.quality_report
            ctx.passes_quality_gate = result.passes_quality_gate
            ctx.state = OrchestratorState.ASSESSING
            ctx.add_stage_reason(
                "assess_evidence",
                f"gate={'pass' if result.passes_quality_gate else 'fail'}",
            )

        elif action == OrchestratorAction.DECIDE:
            ctx.decision_result = result.decision_result
            ctx.answer_lane = (
                result.decision_result.resolved_lane() if result.decision_result else None
            )
            ctx.state = OrchestratorState.DECIDING
            ctx.add_stage_reason(
                "decide",
                result.decision_result.reason if result.decision_result else "unknown",
            )

        elif action == OrchestratorAction.GENERATE:
            ctx.answer = result.answer
            ctx.citations = result.citations
            ctx.followup = result.followup
            ctx.confidence = result.confidence
            ctx.answer_plan = result.answer_plan
            ctx.generated_decision = result.generated_decision
            ctx.state = OrchestratorState.GENERATING
            ctx.add_stage_reason(
                "generate",
                f"llm_complete decision={result.generated_decision or 'unknown'}",
            )

        elif action == OrchestratorAction.VERIFY:
            rr = result.reviewer_result
            status_str = _reviewer_status_to_str(rr) or "unknown"
            status_map = {
                "PASS": "accept",
                "ASK_USER": "downgrade_lane",
                "ESCALATE": "escalate",
                "TRIM_UNSUPPORTED": "trim_unsupported_claims",
                "DOWNGRADE_LANE": "downgrade_lane",
            }
            schema_status = status_map.get(status_str, status_str.lower())
            if status_str == "TRIM_UNSUPPORTED" and getattr(rr, "trimmed_answer", None):
                ctx.answer = rr.trimmed_answer
            if status_str == "DOWNGRADE_LANE":
                ctx.answer_lane = getattr(rr, "final_lane", None) or "PASS_WEAK"
            ctx.review_result = ReviewResult(
                status=schema_status,
                unsupported_claims=getattr(rr, "unsupported_claims", None) or getattr(rr, "missing_fields", []) or [],
                weakly_supported_claims=getattr(rr, "weakly_supported_claims", []) or [],
                claim_to_citation_map=getattr(rr, "claim_to_citation_map", {}) or {},
                reviewer_notes=getattr(rr, "reasons", []) or [],
                final_lane=getattr(rr, "final_lane", None) or status_str,
                suggested_retry_plan=None,
            )
            ctx.state = OrchestratorState.REVIEWING
            ctx.add_stage_reason("verify", _reviewer_status_to_str(rr) or "unknown")

        elif action == OrchestratorAction.RETRY_RETRIEVE:
            ctx.retrieval_attempt += 1
            ctx.state = OrchestratorState.RETRYING
            ctx.add_stage_reason("retry_retrieve", f"attempt={ctx.retrieval_attempt}")

    async def run(
        self,
        ctx: OrchestratorContext,
        handlers: OrchestratorHandlers,
    ) -> Any:
        """Drive the pipeline until a terminal action. Returns handlers.build_output()."""
        terminal_actions = {
            OrchestratorAction.DONE,
            OrchestratorAction.ASK_USER,
            OrchestratorAction.ESCALATE,
        }
        max_iterations = 50
        iterations = 0

        while iterations < max_iterations:
            iterations += 1
            has_evidence = bool(ctx.evidence)
            reviewer_status = _reviewer_status_to_str(
                getattr(ctx, "_last_reviewer_result", None)
            )

            action = self.next_action(ctx, reviewer_status, has_evidence)

            if action in terminal_actions:
                ctx.termination_reason = action.value
                ctx.state = OrchestratorState.COMPLETE
                try:
                    from app.services.flow_debug import _pipeline_log
                    _pipeline_log("orchestrator", "terminated", action=action.value, trace_id=ctx.trace_id, stage_reasons=ctx.stage_reasons[-5:])
                except Exception:
                    pass
                logger.debug(
                    "orchestrator_terminated",
                    action=action.value,
                    trace_id=ctx.trace_id,
                    stage_reasons=ctx.stage_reasons[-5:],
                )
                return await handlers.build_output(ctx, action)

            if action == OrchestratorAction.RETRY_RETRIEVE:
                self._apply_result(ctx, action, PhaseResult())
            else:
                try:
                    try:
                        from app.services.flow_debug import _pipeline_log
                        _pipeline_log("orchestrator", "execute", action=action.value, state=ctx.state.value, trace_id=ctx.trace_id)
                    except Exception:
                        pass
                    result = await handlers.execute(ctx, action)
                except Exception as e:
                    logger.error("orchestrator_execute_failed", action=action.value, error=str(e))
                    ctx.extra["error"] = str(e)
                    return await handlers.build_output(ctx, OrchestratorAction.ESCALATE)
                self._apply_result(ctx, action, result)
                if action == OrchestratorAction.VERIFY and result.reviewer_result:
                    ctx._last_reviewer_result = result.reviewer_result

        ctx.termination_reason = "max_iterations"
        ctx.state = OrchestratorState.COMPLETE
        logger.warning("orchestrator_max_iterations", trace_id=ctx.trace_id)
        return await handlers.build_output(ctx, OrchestratorAction.ASK_USER)
