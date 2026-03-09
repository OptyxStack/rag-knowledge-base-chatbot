"""Intent-based shortcut cache for common queries.

Returns predefined answers without calling LLM/retrieval. Configurable via env.
"""

import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class IntentMatch:
    """Result of intent matching."""

    intent: str
    answer: str


def _default_intents() -> dict[str, dict[str, str]]:
    app_name = (get_settings().app_name or "").strip()
    prefix = f"{app_name}'s " if app_name else ""
    welcome = f"Welcome to {app_name} support. " if app_name else "Welcome. "
    return {
        "what_can_you_do": {
            "patterns": r"\b(what (can you|do you|does (this )?ai) do|bạn làm gì|ai làm gì|chức năng)\b",
            "answer": f"I'm {prefix}AI support assistant. I can help with questions about products, policies, and setup guides. What would you like to know?",
        },
        "who_are_you": {
            "patterns": r"\b(who are you|bạn là ai|ai là gì)\b",
            "answer": f"I'm {prefix}AI support assistant. I answer questions using the provided documentation. How can I help?",
        },
        "who_am_i": {
            "patterns": r"\b(who am i|tôi là ai|mình là ai)\b",
            "answer": "I don't have access to your account details. For billing or account management, please log in to your account or contact support.",
        },
        "about": {
            "patterns": r"\b(what is|about|who are you|giới thiệu)\s+(?:this (?:company|service)|us|your (?:company|service))\b",
            "answer": f"I'm {prefix}AI support assistant. I help answer questions using our documentation. What would you like to know?",
        },
        "hello": {
            "patterns": r"^(hi|hello|hey|chào|xin chào)\s*!?$",
            "answer": f"Hello! {welcome}How can I help you today?",
        },
    }


def _load_intent_responses() -> dict[str, dict[str, str]]:
    """Load intent config from settings or use defaults."""
    return _default_intents()


def match_intent(query: str) -> IntentMatch | None:
    """Check if query matches a cached intent. Returns (intent, answer) or None."""
    settings = get_settings()
    if not getattr(settings, "intent_cache_enabled", True):
        return None

    q = query.strip().lower()
    if len(q) > 200:
        return None

    intents = _load_intent_responses()
    for intent_key, config in intents.items():
        patterns = config.get("patterns", "")
        answer = config.get("answer", "")
        if not patterns or not answer:
            continue
        try:
            if re.search(patterns, q, re.IGNORECASE):
                return IntentMatch(intent=intent_key, answer=answer)
        except re.error:
            logger.warning("intent_pattern_invalid", intent=intent_key, pattern=patterns)
            continue
    return None
