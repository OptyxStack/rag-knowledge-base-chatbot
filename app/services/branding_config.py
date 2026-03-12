"""Load prompts and intents from DB with in-memory cache.

Branding, system prompt, and intent cache are stored in app_config and intents tables.
Cache is refreshed on startup and can be invalidated via refresh_cache().

Prompt layering (Core + Domain + Custom):
- Core: non-overridable rules (evidence-only, cite sources, JSON output)
- Domain: preset (support | legal | generic) or custom from DB
- Custom: optional admin-defined rules from app_config.custom_prompt_rules
"""

import re
import time
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import AppConfig, Intent

logger = get_logger(__name__)

# --- Core rules (always enforced, domain-agnostic) ---
CORE_RULES = """You are a RAG assistant. Ground all factual claims (prices, links, policy, specs, steps) in the provided evidence. Do not invent or guess facts.

CORE RULES (always enforced):
1. For facts, prices, links, policy, specs: use ONLY the evidence. Do not add or infer facts from your training.
2. When listing items, include ONLY what is explicitly named in the evidence. Never infer or add similar items.
3. Always cite your sources. Put citations ONLY in the citations array (chunk_id, source_url, doc_type). NEVER write chunk_id, source_url, (Chunk uuid, url), or [chunk_id, url] in the answer text—readers must not see internal citation metadata.
4. If you cite a chunk, it MUST be in the evidence list.
5. Respond with valid JSON matching the output schema. No markdown, no extra text—only the JSON object."""

# --- Domain presets (config: support | legal | generic) ---
DOMAIN_SUPPORT = """
DOMAIN RULES (support / plans / pricing):
- When the user asks about plans, products, or pricing: ALWAYS include (1) plan names, (2) prices/specs, and (3) the actual links (source_url or order_link from evidence). Format like: "Plan X: $Y – [link]". Do not give a generic answer without links.
- If the evidence only partially answers the question, provide a bounded partial answer with decision set to PASS. Clearly separate confirmed details from unverified details. Use ASK_USER only when no safe bounded answer can be given.
- Use the evidence. When evidence contains information relevant to the query—policy, terms, eligibility, exclusions, steps, specs, links—extract, quote, or paraphrase it. Do not say "I cannot provide", "please refer to", or "contact support" when the answer is already in the evidence. Answer from the evidence.
- ESCALATE only when evidence is empty or clearly irrelevant. ASK_USER only when the query is ambiguous or evidence is insufficient and no safe partial answer exists. When evidence has usable content, answer from it.
- TONE: Speak as part of the company—use "we", "our", "us". Avoid third-person or detached phrasing (e.g. "The evidence says" → "Based on our documentation, we offer..."; "Yes—according to the Terms of Service" → "Yes—our Terms of Service allow..."). Be helpful and direct, as if you belong to the support team.
- EVIDENCE SOURCE: The evidence comes from our knowledge base, not from the user. Never say "evidence you shared", "information you provided", or "what you sent"—use "our documentation", "the information we have", or "our available sources" instead."""

DOMAIN_LEGAL = """
DOMAIN RULES (legal / policy):
- For policy, terms, or legal questions: quote or paraphrase only from evidence. Do not interpret legal language.
- For high-risk topics (refunds, disputes, liability), if you cannot find clear policy evidence, set decision to ESCALATE.
- Always cite the specific document/source for legal claims."""

DOMAIN_GENERIC = """
DOMAIN RULES: (minimal—no extra domain-specific rules)"""

DOMAIN_PRESETS: dict[str, str] = {
    "support": DOMAIN_SUPPORT,
    "legal": DOMAIN_LEGAL,
    "generic": DOMAIN_GENERIC,
}

OUTPUT_SCHEMA = """
OUTPUT SCHEMA (JSON):
{
  "decision": "PASS" | "ASK_USER" | "ESCALATE",
  "answer": "your grounded answer",
  "followup_questions": ["question1", "question2"],
  "citations": [{"chunk_id": "...", "source_url": "...", "doc_type": "..."}],
  "confidence": 0.0 to 1.0
}

Evidence chunks will be provided in the user message."""

# Legacy fallback (full prompt) for backward compat when DB has no layered config
FALLBACK_SYSTEM_PROMPT = (
    "You are a support assistant speaking on behalf of the company. Use 'we', 'our', and 'us'—you belong to the team.\n\n"
    + CORE_RULES + DOMAIN_SUPPORT + OUTPUT_SCHEMA
)

LANE_AWARE_PROMPT_SUFFIX = """

INTERNAL ROUTING NOTES:
- The runtime calibrates output mode as PASS_EXACT, PASS_PARTIAL, or ASK_USER.
- PASS_PARTIAL is a bounded-answer mode. In JSON output, still use decision="PASS".
- For PASS_PARTIAL: use natural, client-friendly language. When unclear, say briefly (e.g. "We don't have that")—no long disclaimers or lists of missing items. At most one short follow-up question.
"""

def _get_fallback_intents() -> list[tuple[str, str, str]]:
    """Generic fallback intents. Uses APP_NAME from config when set. Customize via Admin API."""
    app_name = get_settings().app_name.strip()
    prefix = f"{app_name}'s " if app_name else ""
    welcome = f"Welcome to {app_name} support. " if app_name else "Welcome. "
    return [
        ("what_can_you_do", r"\b(what (can you|do you|does (this )?ai) do|bạn làm gì|ai làm gì|chức năng)\b", f"I'm {prefix}AI support assistant. I can help with questions about products, policies, and setup guides. What would you like to know?"),
        ("who_are_you", r"\b(who are you|bạn là ai|ai là gì)\b", f"I'm {prefix}AI support assistant. I answer questions using the provided documentation. How can I help?"),
        ("who_am_i", r"\b(who am i|tôi là ai|mình là ai)\b", "I don't have access to your account details. For billing or account management, please log in to your account or contact support."),
        ("about", r"\b(what is|about|who are you|giới thiệu)\s+(?:this (?:company|service)|us|your (?:company|service))\b", f"I'm {prefix}AI support assistant. I help answer questions using our documentation. What would you like to know?"),
        ("hello", r"^(hi|hello|hey|chào|xin chào)\s*!?$", f"Hello! {welcome}How can I help you today?"),
    ]


@dataclass
class IntentMatch:
    """Result of intent matching."""

    intent: str
    answer: str


# In-memory cache
_cache: dict[str, Any] = {
    "persona": None,
    "prompt_domain": None,
    "custom_prompt_rules": None,
    "use_legacy_full_prompt": False,
    "intents": None,
    "updated_at": 0.0,
}
CACHE_TTL_SECONDS = 60  # Refresh every 60s if stale

PROMPT_CONFIG_KEYS = ("system_prompt", "prompt_domain", "custom_prompt_rules")


def _build_layered_prompt(
    persona: str | None,
    domain: Literal["support", "legal", "generic"],
    custom_rules: str | None,
) -> str:
    """Build system prompt from Core + Domain + Custom layers."""
    parts: list[str] = []
    if persona and persona.strip():
        parts.append(persona.strip())
    parts.append(CORE_RULES)
    domain_rules = DOMAIN_PRESETS.get(domain, DOMAIN_GENERIC)
    parts.append(domain_rules)
    if custom_rules and custom_rules.strip():
        parts.append("\nCUSTOM RULES (admin-defined):\n" + custom_rules.strip())
    parts.append(OUTPUT_SCHEMA)
    prompt = "\n\n".join(parts)
    if "PASS_PARTIAL is a bounded-answer mode" not in prompt:
        prompt = f"{prompt.rstrip()}\n{LANE_AWARE_PROMPT_SUFFIX}".strip()
    return prompt


async def _load_from_db(session: AsyncSession) -> tuple[dict[str, Any], list[tuple[str, str, str]]]:
    """Load prompt config and intents from DB."""
    persona: str | None = None
    prompt_domain: str | None = None
    custom_prompt_rules: str | None = None
    intents: list[tuple[str, str, str]] = []

    try:
        result = await session.execute(
            select(AppConfig.key, AppConfig.value).where(
                AppConfig.key.in_(PROMPT_CONFIG_KEYS)
            )
        )
        for key, value in result.all():
            if key == "system_prompt" and value:
                persona = value
            elif key == "prompt_domain" and value:
                v = value.strip().lower()
                if v in ("support", "legal", "generic"):
                    prompt_domain = v
            elif key == "custom_prompt_rules" and value:
                custom_prompt_rules = value

        # Intents (enabled only, ordered by sort_order)
        result = await session.execute(
            select(Intent.key, Intent.patterns, Intent.answer)
            .where(Intent.enabled == True)
            .order_by(Intent.sort_order)
        )
        intents = [(r.key, r.patterns, r.answer) for r in result.all()]
        if not intents:
            intents = _get_fallback_intents()
    except Exception as e:
        logger.warning("branding_config_load_failed", error=str(e))
        intents = _get_fallback_intents()

    settings = get_settings()
    domain = (prompt_domain or getattr(settings, "prompt_domain", "support")) or "support"
    if domain not in ("support", "legal", "generic"):
        domain = "support"

    # Backward compat: if system_prompt looks like legacy full prompt, keep as full override
    is_legacy_full = (
        persona is not None
        and len(persona) > 400
        and ("OUTPUT SCHEMA" in persona or "RULES:" in persona)
    )

    return {
        "persona": persona or "You are a support assistant speaking on behalf of the company. Use 'we', 'our', and 'us' when referring to the service—you belong to the team.",
        "prompt_domain": domain,
        "custom_prompt_rules": custom_prompt_rules,
        "use_legacy_full_prompt": is_legacy_full,
    }, intents


async def refresh_cache(session: AsyncSession) -> None:
    """Load config from DB and update in-memory cache."""
    prompt_cfg, intents = await _load_from_db(session)
    _cache["persona"] = prompt_cfg["persona"]
    _cache["prompt_domain"] = prompt_cfg["prompt_domain"]
    _cache["custom_prompt_rules"] = prompt_cfg["custom_prompt_rules"]
    _cache["use_legacy_full_prompt"] = prompt_cfg.get("use_legacy_full_prompt", False)
    _cache["intents"] = intents
    _cache["updated_at"] = time.monotonic()
    logger.info(
        "branding_config_cache_refreshed",
        intents_count=len(intents),
        prompt_domain=prompt_cfg["prompt_domain"],
    )


def get_system_prompt() -> str:
    """Return cached system prompt (Core + Domain + Custom). Falls back to FALLBACK if cache empty."""
    persona = _cache.get("persona")
    domain = _cache.get("prompt_domain")
    custom_rules = _cache.get("custom_prompt_rules")
    use_legacy = _cache.get("use_legacy_full_prompt", False)

    if use_legacy and persona:
        prompt = persona
    elif persona is None and domain is None:
        prompt = FALLBACK_SYSTEM_PROMPT
    else:
        domain = domain or getattr(get_settings(), "prompt_domain", "support")
        if domain not in ("support", "legal", "generic"):
            domain = "support"
        persona = persona or "You are a support assistant speaking on behalf of the company. Use 'we', 'our', and 'us' when referring to the service—you belong to the team."
        prompt = _build_layered_prompt(persona, domain, custom_rules)

    if "PASS_PARTIAL is a bounded-answer mode" not in prompt:
        prompt = f"{prompt.rstrip()}\n{LANE_AWARE_PROMPT_SUFFIX}".strip()
    return prompt


def get_intents() -> list[tuple[str, str, str]]:
    """Return cached intents as (key, patterns, answer). Falls back if cache empty."""
    intents = _cache.get("intents")
    if intents is None:
        return _get_fallback_intents()
    return intents


def match_intent(query: str) -> IntentMatch | None:
    """Check if query matches a cached intent. Returns IntentMatch or None."""
    settings = get_settings()
    if not getattr(settings, "intent_cache_enabled", True):
        return None
    disabled_keys = {
        str(key).strip().lower()
        for key in (getattr(settings, "intent_cache_disabled_keys", None) or [])
        if str(key).strip()
    }

    q = query.strip().lower()
    if len(q) > 200:
        return None

    intents = get_intents()
    for intent_key, patterns, answer in intents:
        if intent_key.strip().lower() in disabled_keys:
            continue
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
    if _cache.get("persona") is None and _cache.get("prompt_domain") is None:
        return True
    elapsed = time.monotonic() - _cache.get("updated_at", 0)
    return elapsed > CACHE_TTL_SECONDS
