"""Answer generation with grounding and reviewer gate.

Orchestrates the RAG pipeline via Orchestrator. Phase logic lives in
app.services.phases; helpers in answer_utils and flow_debug.
"""

from app.core.config import get_settings
from app.services.flow_debug import _pipeline_log
from app.core.logging import get_logger
from app.services.branding_config import match_intent
from app.services.llm_config import get_llm_fallback_model, get_llm_model
from app.services.llm_gateway import LLMGateway, get_llm_gateway
from app.services.orchestrator import (
    Orchestrator,
    OrchestratorAction,
    OrchestratorContext,
    OrchestratorState,
    PhaseResult,
)
from app.services.archi_config import get_language_detect_enabled
from app.services.language_detect import detect_language
from app.services.normalizer import normalize as normalize_query
from app.services.output_builder import build_output as build_answer_output
from app.services.phases import (
    execute_assess_evidence,
    execute_decide,
    execute_generate,
    execute_retrieve,
    execute_verify,
)
from app.services.retrieval import RetrievalService
from app.services.schemas import AnswerOutput, QuerySpec
from app.services.reviewer import ReviewerGate

__all__ = ["AnswerOutput", "AnswerService"]

logger = get_logger(__name__)


class AnswerService:
    """Orchestrates retrieval, LLM generation, and reviewer gate."""

    def __init__(
        self,
        retrieval: RetrievalService | None = None,
        llm: LLMGateway | None = None,
        reviewer: ReviewerGate | None = None,
        orchestrator: Orchestrator | None = None,
    ) -> None:
        self._settings = get_settings()
        self._retrieval = retrieval or RetrievalService()
        self._llm = llm or get_llm_gateway()
        self._reviewer = reviewer or ReviewerGate()
        self._orchestrator = orchestrator or Orchestrator(
            primary_model=get_llm_model(),
            fallback_model=get_llm_fallback_model(),
        )

    async def generate(
        self,
        query: str,
        conversation_history: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
    ) -> AnswerOutput:
        """Generate grounded answer with retrieval and reviewer gate."""
        from app.core.tracing import llm_usage_var, llm_call_log_var
        llm_usage_var.set([])
        from app.services.archi_config import get_debug_llm_calls
        if get_debug_llm_calls():
            llm_call_log_var.set([])

        _pipeline_log("answer_service", "start", query=query[:80], history_len=len(conversation_history or []), trace_id=trace_id)

        intent = match_intent(query)
        if intent:
            _pipeline_log("answer_service", "intent_cache_hit", intent=intent.intent, trace_id=trace_id)
            logger.debug("intent_cache_hit", intent=intent.intent)
            return AnswerOutput(
                decision="PASS",
                answer=intent.answer,
                followup_questions=[],
                citations=[],
                confidence=1.0,
                debug={"trace_id": trace_id, "intent_cache": intent.intent},
            )

        source_lang = detect_language(query) if get_language_detect_enabled() else "en"
        query_spec: QuerySpec | None = None
        if getattr(self._settings, "normalizer_enabled", True):
            query_spec = await normalize_query(query, conversation_history, source_lang=source_lang)
            if query_spec:
                _pipeline_log(
                    "normalizer", "done",
                    intent=query_spec.intent,
                    user_goal=getattr(query_spec, "user_goal", ""),
                    required_evidence=query_spec.required_evidence,
                    hard_requirements=getattr(query_spec, "hard_requirements", []),
                    keyword_queries=(query_spec.keyword_queries or [])[:1],
                    retrieval_profile=getattr(query_spec, "retrieval_profile", ""),
                    trace_id=trace_id,
                )

        effective_query = query
        if query_spec and query_spec.canonical_query_en:
            effective_query = query_spec.canonical_query_en

        if query_spec and query_spec.skip_retrieval:
            # Routine question: respond immediately with canned_response, no retrieval or LLM
            _pipeline_log("answer_service", "skip_retrieval_canned", trace_id=trace_id)
            canned = (query_spec.canned_response or "").strip()
            if not canned:
                app_name = (self._settings.app_name or "").strip()
                canned = f"Hello! Welcome to {app_name} support. How can I help you today?" if app_name else "Hello! How can I help you today?"
            return AnswerOutput(
                decision="PASS",
                answer=canned,
                followup_questions=[],
                citations=[],
                confidence=1.0,
                debug={"trace_id": trace_id, "skip_retrieval": True},
            )

        required_evidence = (
            query_spec.required_evidence if query_spec else []
        )
        hard_requirements = (
            (query_spec.hard_requirements or [])
            if query_spec and getattr(query_spec, "hard_requirements", None) is not None
            else []
        )

        fallback_hypothesis_count = len(getattr(query_spec, "fallback_hypotheses", None) or []) if query_spec else 0
        max_attempts = max(self._settings.max_retrieval_attempts, 1 + fallback_hypothesis_count)
        _pipeline_log(
            "answer_service", "context_created",
            intent=getattr(query_spec, "intent", ""),
            user_goal=getattr(query_spec, "user_goal", ""),
            required_evidence=required_evidence,
            hard_requirements=hard_requirements,
            evidence_families=getattr(query_spec, "evidence_families", []),
            answer_shape=getattr(query_spec, "answer_shape", "direct_lookup"),
            effective_query=effective_query[:80],
            trace_id=trace_id,
        )
        ctx = OrchestratorContext(
            query=query,
            state=OrchestratorState.UNDERSTANDING,
            trace_id=trace_id,
            conversation_history=conversation_history or [],
            max_attempts=max_attempts,
            query_spec=query_spec,
            effective_query=effective_query,
            source_lang=source_lang,
            extra={
                "required_evidence": required_evidence,
                "hard_requirements": hard_requirements,
                "primary_hypothesis_name": getattr(getattr(query_spec, "primary_hypothesis", None), "name", "primary") if query_spec else "primary",
            },
        )

        return await self._orchestrator.run(ctx, self)

    async def _execute_understand(self, ctx: OrchestratorContext) -> PhaseResult:
        """UNDERSTAND: already done before orchestrator."""
        return PhaseResult(
            query_spec=ctx.query_spec,
            effective_query=ctx.effective_query or ctx.query,
            source_lang=ctx.source_lang,
        )

    async def execute(
        self, ctx: OrchestratorContext, action: OrchestratorAction
    ) -> PhaseResult:
        """Execute phase per OrchestratorHandlers protocol."""
        if action == OrchestratorAction.UNDERSTAND:
            return await self._execute_understand(ctx)
        if action == OrchestratorAction.RETRIEVE:
            return await execute_retrieve(
                ctx,
                retrieval=self._retrieval,
                orchestrator=self._orchestrator,
                settings=self._settings,
            )
        if action == OrchestratorAction.ASSESS_EVIDENCE:
            return await execute_assess_evidence(ctx)
        if action == OrchestratorAction.DECIDE:
            return await execute_decide(ctx)
        if action == OrchestratorAction.GENERATE:
            return await execute_generate(
                ctx,
                llm=self._llm,
                orchestrator=self._orchestrator,
                settings=self._settings,
            )
        if action == OrchestratorAction.VERIFY:
            return await execute_verify(ctx, reviewer=self._reviewer)
        return PhaseResult()

    async def build_output(
        self, ctx: OrchestratorContext, action: OrchestratorAction
    ) -> AnswerOutput:
        """Build AnswerOutput for terminal actions per OrchestratorHandlers protocol."""
        return await build_answer_output(
            ctx,
            action,
            get_model_for_query=self._orchestrator.get_model_for_query,
        )
