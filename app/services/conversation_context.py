"""Conversation context truncation. Config-driven limits."""

from app.core.config import get_settings


def truncate_for_pipeline(
    history: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """Truncate to last N messages for pipeline input (API layer)."""
    if not history:
        return []
    max_m = get_settings().conversation_history_max_messages
    return list(history[-max_m:])


def truncate_for_prompt(
    history: list[dict[str, str]] | None,
) -> list[dict[str, str]]:
    """Truncate to last N messages for LLM prompts (normalizer, generate, query_rewriter)."""
    if not history:
        return []
    max_m = get_settings().conversation_history_max_for_prompt
    return list(history[-max_m:])
