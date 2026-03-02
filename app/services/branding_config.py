"""Load prompts and intents from DB with in-memory cache.

Branding, system prompt, and intent cache are stored in app_config and intents tables.
Cache is refreshed on startup and can be invalidated via refresh_cache().
"""

import re
import time
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import AppConfig, Intent

logger = get_logger(__name__)

# Fallback when DB is empty or unavailable (matches original hardcoded values)
FALLBACK_SYSTEM_PROMPT = """You are GreenCloud's support assistant. GreenCloud is a VPS and dedicated server provider (Windows, Linux KVM, macOS VPS). You must ONLY use the provided evidence to answer. Never guess or make up information.

RULES:
1. Use ONLY the provided evidence chunks. Do not add information from your training.
2. When listing items (products, features, options), include ONLY what is explicitly named in the evidence. Never infer or add similar items.
3. When the user asks about plans, products, or pricing: ALWAYS include (1) plan names, (2) prices/specs, and (3) the actual links (source_url or order_link from evidence). Format like: "Plan X: $Y – [link]". Do not give a generic answer without links.
4. If the evidence is insufficient to answer, set decision to ASK_USER and provide 1-3 concise follow-up questions to clarify.
5. For high-risk topics (refunds, billing disputes, legal, abuse), if you cannot find clear policy evidence, set decision to ESCALATE.
6. Always cite your sources. For each key claim, include a citation with chunk_id and source_url.
7. If you cite a chunk, it MUST be in the evidence list.
8. For plan/pricing questions: extract and include any URLs from evidence (Source, Order, order_link). Users want direct links to order or view plans.
9. Respond with valid JSON matching the output schema. No markdown, no extra text—only the JSON object.

OUTPUT SCHEMA (JSON):
{
  "decision": "PASS" | "ASK_USER" | "ESCALATE",
  "answer": "your grounded answer",
  "followup_questions": ["question1", "question2"],
  "citations": [{"chunk_id": "...", "source_url": "...", "doc_type": "..."}],
  "confidence": 0.0 to 1.0
}

Evidence chunks will be provided in the user message."""

FALLBACK_INTENTS: list[tuple[str, str, str]] = [
    ("what_can_you_do", r"\b(what (can you|do you|does (this )?ai) do|bạn làm gì|ai làm gì|chức năng)\b", "I'm GreenCloud's AI support assistant. I can help with questions about our VPS (Windows, Linux KVM, macOS), dedicated servers, pricing, setup guides, and policies. Our docs are at https://green.cloud/docs. What would you like to know?"),
    ("who_are_you", r"\b(who are you|bạn là ai|ai là gì)\b", "I'm GreenCloud's AI support assistant. GreenCloud is a leading VPS and dedicated server provider (founded 2013), offering Windows VPS, KVM Linux VPS, macOS VPS, and bare-metal servers. I answer questions using our documentation at https://green.cloud/docs."),
    ("who_am_i", r"\b(who am i|tôi là ai|mình là ai)\b", "I don't have access to your GreenCloud account details. For billing, account info, or service management, please log in at https://greencloudvps.com/billing or contact our 24/7 support (average response: 9 minutes)."),
    ("about_greencloud", r"\b(what is greencloud|about greencloud|greencloud là gì|giới thiệu greencloud)\b", "GreenCloud is an Infrastructure as a Service provider founded in 2013. We offer: Windows VPS (from $8/mo), KVM Linux VPS (from $6/mo), macOS VPS (from $22/mo), and dedicated servers (from $110/mo). 99.99% uptime, 24/7 in-house support (9-min avg response), 30 locations across 4 continents. Docs: https://green.cloud/docs"),
    ("refund_policy", r"\b(do you have|do u have|what(?:'s| is)|tell me about)\s+(?:a |your? )?refund\s*policy\b|\brefund\s*policy\??\s*$|chính sách hoàn tiền", "Yes. GreenCloud has a refund policy for the first VPS of new (\"fresh\") clients: if you're not happy with the service, you may cancel and request a refund within the first 7 days of purchase. Special discounted plans are excluded from the refund policy. For the full explanation and additional conditions, refer to the Terms of Service."),
    ("hello", r"^(hi|hello|hey|chào|xin chào)\s*!?$", "Hello! Welcome to GreenCloud support. I can help with VPS, dedicated servers, pricing, or how-to guides. What do you need?"),
]


@dataclass
class IntentMatch:
    """Result of intent matching."""

    intent: str
    answer: str


# In-memory cache
_cache: dict[str, Any] = {
    "system_prompt": None,
    "intents": None,
    "updated_at": 0.0,
}
CACHE_TTL_SECONDS = 60  # Refresh every 60s if stale


async def _load_from_db(session: AsyncSession) -> tuple[str, list[tuple[str, str, str]]]:
    """Load system prompt and intents from DB."""
    prompt = FALLBACK_SYSTEM_PROMPT
    intents: list[tuple[str, str, str]] = []

    try:
        # System prompt
        result = await session.execute(
            select(AppConfig.value).where(AppConfig.key == "system_prompt").limit(1)
        )
        row = result.scalar_one_or_none()
        if row:
            prompt = row

        # Intents (enabled only, ordered by sort_order)
        result = await session.execute(
            select(Intent.key, Intent.patterns, Intent.answer)
            .where(Intent.enabled == True)
            .order_by(Intent.sort_order)
        )
        intents = [(r.key, r.patterns, r.answer) for r in result.all()]
        if not intents:
            intents = FALLBACK_INTENTS
    except Exception as e:
        logger.warning("branding_config_load_failed", error=str(e))
        intents = FALLBACK_INTENTS

    return prompt, intents


async def refresh_cache(session: AsyncSession) -> None:
    """Load config from DB and update in-memory cache."""
    prompt, intents = await _load_from_db(session)
    _cache["system_prompt"] = prompt
    _cache["intents"] = intents
    _cache["updated_at"] = time.monotonic()
    logger.info("branding_config_cache_refreshed", intents_count=len(intents))


def get_system_prompt() -> str:
    """Return cached system prompt. Falls back to FALLBACK if cache empty."""
    prompt = _cache.get("system_prompt")
    if prompt is None:
        return FALLBACK_SYSTEM_PROMPT
    return prompt


def get_intents() -> list[tuple[str, str, str]]:
    """Return cached intents as (key, patterns, answer). Falls back if cache empty."""
    intents = _cache.get("intents")
    if intents is None:
        return FALLBACK_INTENTS
    return intents


def match_intent(query: str) -> IntentMatch | None:
    """Check if query matches a cached intent. Returns IntentMatch or None."""
    settings = get_settings()
    if not getattr(settings, "intent_cache_enabled", True):
        return None

    q = query.strip().lower()
    if len(q) > 200:
        return None

    intents = get_intents()
    for intent_key, patterns, answer in intents:
        if not patterns or not answer:
            continue
        try:
            if re.search(patterns, q, re.IGNORECASE):
                return IntentMatch(intent=intent_key, answer=answer)
        except re.error:
            logger.warning("intent_pattern_invalid", intent=intent_key, pattern=patterns)
            continue
    return None


def is_cache_stale() -> bool:
    """True if cache is empty or TTL exceeded."""
    if _cache.get("system_prompt") is None:
        return True
    elapsed = time.monotonic() - _cache.get("updated_at", 0)
    return elapsed > CACHE_TTL_SECONDS
