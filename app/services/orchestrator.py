"""Orchestrator: workflow state machine, model routing, retry loop."""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from app.core.logging import get_logger
from app.services.llm_config import get_llm_fallback_model, get_llm_model

logger = get_logger(__name__)


class OrchestratorAction(str, Enum):
    """Next action in workflow."""
    RETRIEVE = "retrieve"
    GENERATE = "generate"
    ASK_USER = "ask_user"
    ESCALATE = "escalate"
    RETRY_RETRIEVE = "retry_retrieve"
    DONE = "done"


class OrchestratorState(str, Enum):
    """Workflow state."""
    INIT = "init"
    RETRIEVING = "retrieving"
    GENERATING = "generating"
    REVIEWING = "reviewing"
    RETRYING = "retrying"
    COMPLETE = "complete"


@dataclass
class OrchestratorContext:
    """Context passed through workflow."""
    query: str
    state: OrchestratorState = OrchestratorState.INIT
    attempt: int = 1
    max_attempts: int = 2
    evidence: list[Any] = field(default_factory=list)
    model_override: str | None = None
    retrieval_attempt: int = 0


def route_model(query: str, available_models: list[str]) -> str:
    """Route to model based on query. Use primary (stronger) model by default for accuracy."""
    if not available_models:
        return get_llm_model()
    # Primary model for all queries - better accuracy; fallback only on API failure
    return available_models[0]


class Orchestrator:
    """State machine orchestrator for support flow."""

    def __init__(
        self,
        primary_model: str | None = None,
        fallback_model: str | None = None,
    ):
        self.primary_model = primary_model or get_llm_model()
        self.fallback_model = fallback_model or get_llm_fallback_model()
        self.models = [self.primary_model, self.fallback_model]

    def get_model_for_query(self, query: str) -> str:
        """Model routing based on query complexity."""
        return route_model(query, [self.primary_model, self.fallback_model])

    def next_action(
        self,
        ctx: OrchestratorContext,
        reviewer_status: str | None = None,
        has_evidence: bool = False,
    ) -> OrchestratorAction:
        """Determine next action from current state and reviewer result."""
        if ctx.state == OrchestratorState.INIT:
            return OrchestratorAction.RETRIEVE

        if ctx.state == OrchestratorState.RETRIEVING:
            if has_evidence:
                return OrchestratorAction.GENERATE
            return OrchestratorAction.ASK_USER

        if ctx.state == OrchestratorState.REVIEWING:
            if reviewer_status == "PASS":
                return OrchestratorAction.DONE
            if reviewer_status == "ESCALATE":
                return OrchestratorAction.ESCALATE
            if reviewer_status == "ASK_USER":
                return OrchestratorAction.ASK_USER
            if reviewer_status == "RETRIEVE_MORE" and ctx.retrieval_attempt < ctx.max_attempts:
                return OrchestratorAction.RETRY_RETRIEVE
            return OrchestratorAction.ASK_USER

        return OrchestratorAction.DONE
