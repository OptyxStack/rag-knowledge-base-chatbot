"""Task-aware model routing. Important tasks use primary (gpt-5.2); others use economy."""

from app.services.archi_config import (
    get_llm_model_economy,
    get_llm_task_aware_routing_enabled,
)
from app.services.llm_config import get_llm_fallback_model, get_llm_model

# Task importance: high = primary, medium/low = economy
TASK_PRIMARY = frozenset({"generate", "self_critic"})
TASK_ECONOMY = frozenset({
    "normalizer", "decision_router", "evidence_evaluator", "evidence_quality",
    "final_polish", "doc_type_classifier", "query_rewriter", "evidence_selector",
    "branding_auto_generator", "conversation_relevance_check",
})


def get_model_for_task(task: str) -> str:
    """Return model for given task. Primary (gpt-5.2) for critical tasks, economy for rest."""
    if not get_llm_task_aware_routing_enabled():
        return get_llm_model()

    if task in TASK_PRIMARY:
        return get_llm_model()
    if task in TASK_ECONOMY:
        return get_llm_model_economy() or get_llm_fallback_model()
    return get_llm_model()
