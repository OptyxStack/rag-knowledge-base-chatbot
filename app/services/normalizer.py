"""Request Normalizer – Phase 2: QuerySpec from raw query.

Rule-based by default; optional LLM for intent/entities/evidence inference.
Use normalizer_use_llm=True for better accuracy on complex queries.
"""

import json
import re
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm_gateway import get_llm_gateway
from app.services.schemas import QuerySpec

logger = get_logger(__name__)

# LLM prompt for QuerySpec extraction (language-aware: translates if non-English)
NORMALIZER_SYSTEM_PROMPT_BASE = """You are a query analyzer for a VPS/dedicated server support chatbot.

Analyze the user's query and conversation context. Output JSON only, no markdown.

Output schema:
{
  "canonical_query_en": "English translation or original if already English",
  "intent": "transactional|comparison|policy|troubleshooting|account|informational|ambiguous",
  "entities": ["vps", "pricing", "windows", ...],
  "required_evidence": ["numbers_units", "transaction_link", "policy_language", "steps_structure", "has_any_url"],
  "risk_level": "low|medium|high",
  "is_ambiguous": false,
  "clarifying_questions": []
}

Intent rules:
- transactional: price, cost, order, buy, subscribe, mua, giá
- comparison: diff, compare, vs, khác, so sánh
- policy: refund, policy, terms, cancellation, hoàn tiền
- troubleshooting: how, step, setup, cách, hướng dẫn, fix
- account: account, login, billing, tài khoản
- informational: general questions
- ambiguous: referent unclear (e.g. "what diff from this?") when user refers to prior message

Entities: domain terms (vps, dedicated, server, windows, linux, kvm, macos, pricing, plan, nvme, bandwidth, ram, cpu, vds, epyc, xeon, greencloud). Include synonyms if relevant.

required_evidence: only include what the query needs:
- numbers_units: price/cost/specs
- transaction_link: order link, buy link
- policy_language: refund, terms, policy
- steps_structure: how-to, setup guide
- has_any_url: general links

risk_level: high for refund/legal/billing dispute/abuse; medium for cancellation/policy; low otherwise.

is_ambiguous: true if referent unclear (e.g. "this", "that" referring to prior content).

clarifying_questions: 1-3 questions when is_ambiguous; empty otherwise.

canonical_query_en: If the query is NOT in English, translate it to English first. Use this as the canonical form for retrieval. If already English, copy the query as-is."""

# Intent patterns (extended from intent_cache)
INTENT_PATTERNS: list[tuple[str, list[str]]] = [
    ("transactional", ["price", "cost", "pricing", "order", "buy", "subscribe", "mua", "giá", "bao nhiêu"]),
    ("comparison", ["diff", "difference", "compare", "vs", "versus", "khác", "so sánh"]),
    ("policy", ["refund", "policy", "terms", "cancellation", "hoàn tiền", "chính sách"]),
    ("troubleshooting", ["how", "step", "setup", "cách", "hướng dẫn", "fix", "error"]),
    ("account", ["account", "login", "billing", "tài khoản"]),
    ("informational", []),  # default fallback
]

# Risk keywords → risk_level
RISK_HIGH = ["refund", "legal", "billing dispute", "abuse", "chargeback"]
RISK_MEDIUM = ["cancellation", "policy", "terms", "sla"]

# Stopwords: don't add to query context (noise)
CONTEXT_STOPWORDS = {"hello", "hi", "hey", "chào", "xin", "thanks", "thank", "ok", "okay"}

# Queries that need NO retrieval – greetings, social, acknowledgments (allow trailing punct)
SKIP_RETRIEVAL_PATTERN = re.compile(
    r"^(hi|hello|hey|chào|xin\s*chào|thanks|thank\s*you|ok|okay|bye|goodbye|good\s*morning|good\s*afternoon|good\s*evening)\s*[!?.,;:\s\]]*$",
    re.I,
)
DEFAULT_GREETING_RESPONSE = "Hello! Welcome to GreenCloud support. I can help with VPS, dedicated servers, pricing, or how-to guides. What do you need?"

# Ambiguity: referent unclear
AMBIGUITY_PATTERNS = [
    r"what\s+diff(?:erence)?\s+from\s+this",
    r"what(?:'s)?\s+different\s+from\s+this",
    r"what\s+about\s+this",
    r"compare\s+to\s+this",
    r"diff\s+from\s+this",
    r"so\s+what\s+about\s+this",
]
AMBIGUITY_RE = re.compile("|".join(AMBIGUITY_PATTERNS), re.I)

# Citation-like content (user pasted our response)
CITATION_PATTERN = re.compile(r"\[[\w-]+\s+\|\s+https?://", re.I)


def _infer_intent(query: str) -> str:
    q = query.lower().strip()
    for intent, keywords in INTENT_PATTERNS:
        if intent == "informational":
            continue
        if any(kw in q for kw in keywords):
            return intent
    return "informational"


def _extract_entities(query: str) -> list[str]:
    """Extract domain entities from query (simple keyword extraction)."""
    entities: list[str] = []
    q = query.lower()
    domain_terms = [
        "vps", "dedicated", "server", "windows", "linux", "kvm", "macos",
        "pricing", "plan", "plans", "nvme", "bandwidth", "ram", "cpu",
        "vds", "epyc", "xeon", "greencloud",
    ]
    for term in domain_terms:
        if term in q:
            entities.append(term)
    return list(dict.fromkeys(entities))


def _infer_required_evidence(intent: str, query: str) -> list[str]:
    """Infer required_evidence from intent and query."""
    q = query.lower()
    required: list[str] = []
    if intent == "transactional" or any(kw in q for kw in ["price", "cost", "pricing", "giá"]):
        required.extend(["numbers_units", "transaction_link"])
    if intent == "comparison" or any(kw in q for kw in ["diff", "difference", "compare", "khác", "so sánh"]):
        required.extend(["numbers_units", "has_any_url"])
    if any(kw in q for kw in ["link", "order", "mua", "buy", "subscribe"]):
        required.append("transaction_link")
    if intent == "policy" or any(kw in q for kw in ["refund", "policy", "terms"]):
        required.append("policy_language")
    if intent == "troubleshooting" or any(kw in q for kw in ["how", "step", "cách"]):
        required.append("steps_structure")
    return list(dict.fromkeys(required))


def _infer_risk_level(query: str) -> str:
    q = query.lower()
    for kw in RISK_HIGH:
        if kw in q:
            return "high"
    for kw in RISK_MEDIUM:
        if kw in q:
            return "medium"
    return "low"


def _detect_ambiguity(
    query: str,
    conversation_history: list[dict[str, str]] | None,
) -> tuple[bool, list[str]]:
    """Detect if query is ambiguous (referent unclear). Returns (is_ambiguous, clarifying_questions)."""
    q = query.strip()
    # Short query with "this/that" referent
    has_ambiguous_phrase = bool(AMBIGUITY_RE.search(q))
    # User pasted long content (citation format or >200 chars)
    has_pasted_content = len(q) > 200 or bool(CITATION_PATTERN.search(q))
    # Or: short query + last assistant message was long (user referring to our answer)
    last_assistant_long = False
    if conversation_history and not has_pasted_content:
        for m in reversed(conversation_history):
            if m.get("role") == "assistant":
                content = (m.get("content") or "").strip()
                if len(content) > 150:
                    last_assistant_long = True
                break

    is_ambiguous = has_ambiguous_phrase and (has_pasted_content or last_assistant_long or len(q) > 100)
    if not is_ambiguous:
        return False, []

    clarifying = [
        "What would you like to compare this with? Another provider's offer, or a specific plan?",
        "Could you specify what 'this' refers to? For example: a competitor's plan, or a different product?",
    ]
    return True, clarifying


def _build_queries(
    query: str,
    intent: str,
    entities: list[str],
    conversation_history: list[dict[str, str]] | None,
) -> tuple[list[str], list[str]]:
    """Build keyword_queries and semantic_queries for retrieval."""
    # Base: add conversation context terms
    base = query.strip()
    if conversation_history and len(conversation_history) >= 2:
        context_terms: list[str] = []
        for m in conversation_history[-4:]:
            content = (m.get("content") or "").strip()
            if m.get("role") == "user" and content and len(content) < 200:
                words = [
                    w for w in content.split()
                    if len(w) > 2 and w.lower() not in CONTEXT_STOPWORDS
                ][:5]
                context_terms.extend(words)
        if context_terms:
            seen: set[str] = set()
            unique: list[str] = []
            for t in context_terms:
                tl = t.lower()
                if tl not in seen and tl not in base.lower():
                    seen.add(tl)
                    unique.append(t)
            if unique:
                base = f"{' '.join(unique[:3])} {base}".strip()

    semantic = base
    keyword = base

    # Intent-based expansion for BM25
    q_lower = base.lower()
    extras: list[str] = []
    if intent in ("transactional", "comparison") or any(kw in q_lower for kw in ["plan", "price", "pricing", "diff", "compare"]):
        extras.extend(["pricing", "USD", "monthly", "order", "store", "dedicated", "vds"])
    if "refund" in q_lower or "policy" in q_lower:
        extras.extend(["policy", "terms", "30 days"])
    if "support" in q_lower or "help" in q_lower:
        extras.extend(["contact", "email", "FAQ"])
    if entities:
        extras.extend(entities[:3])
    if extras:
        keyword = f"{base} {' '.join(extras[:5])}".strip()

    return [keyword], [semantic]


def _normalize_rule_based(
    query: str,
    conversation_history: list[dict[str, str]] | None,
) -> QuerySpec:
    """Rule-based QuerySpec (no LLM). Used as fallback when LLM disabled or fails."""
    intent = _infer_intent(query)
    entities = _extract_entities(query)
    required_evidence = _infer_required_evidence(intent, query)
    risk_level = _infer_risk_level(query)
    is_ambiguous, clarifying_questions = _detect_ambiguity(query, conversation_history)

    if is_ambiguous:
        intent = "ambiguous"
        clarifying_questions = clarifying_questions[:3]

    keyword_queries, semantic_queries = _build_queries(query, intent, entities, conversation_history)
    constraints: dict[str, Any] = {}

    return QuerySpec(
        intent=intent,
        entities=entities,
        constraints=constraints,
        required_evidence=required_evidence,
        risk_level=risk_level,
        keyword_queries=keyword_queries,
        semantic_queries=semantic_queries,
        clarifying_questions=clarifying_questions,
        is_ambiguous=is_ambiguous,
    )


def _get_normalizer_prompt(source_lang: str | None) -> str:
    """Build system prompt; add translation instruction when source is non-English."""
    base = NORMALIZER_SYSTEM_PROMPT_BASE
    if source_lang and source_lang.lower() != "en":
        base += f"\n\nIMPORTANT: The query is in {source_lang}. You MUST translate it to English and put the translation in canonical_query_en. Then analyze the English version."
    return base


async def _normalize_llm(
    query: str,
    conversation_history: list[dict[str, str]] | None,
    source_lang: str | None = None,
) -> QuerySpec | None:
    """LLM-based QuerySpec. Returns None on error (caller should fallback)."""
    settings = get_settings()
    model = getattr(settings, "normalizer_llm_model", "gpt-4o-mini")
    system_prompt = _get_normalizer_prompt(source_lang)

    user_parts = [f"Query: {query.strip()}"]
    if source_lang and source_lang != "en":
        user_parts.append(f"(Detected language: {source_lang})")
    if conversation_history and len(conversation_history) >= 2:
        ctx = "\n".join(
            f"{m.get('role', 'user')}: {(m.get('content') or '')[:300]}"
            for m in conversation_history[-4:]
        )
        user_parts.append(f"Conversation context:\n{ctx}")

    user_content = "\n\n".join(user_parts)

    try:
        llm = get_llm_gateway()
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            model=model,
            max_tokens=512,
        )
        content = (resp.content or "").strip()
        # Extract JSON (handle markdown code blocks)
        if "```json" in content:
            match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
            content = match.group(1) if match else content
        elif "```" in content:
            match = re.search(r"```\s*([\s\S]*?)\s*```", content)
            content = match.group(1) if match else content

        data = json.loads(content)

        intent = str(data.get("intent", "informational")).lower()
        if intent not in ("transactional", "comparison", "policy", "troubleshooting", "account", "informational", "ambiguous"):
            intent = "informational"

        entities = [str(e) for e in data.get("entities", []) if isinstance(e, str)][:10]
        required_evidence = [str(r) for r in data.get("required_evidence", []) if isinstance(r, str)]
        risk_level = str(data.get("risk_level", "low")).lower()
        if risk_level not in ("low", "medium", "high"):
            risk_level = "low"
        is_ambiguous = bool(data.get("is_ambiguous", False))
        clarifying_questions = [str(q) for q in data.get("clarifying_questions", []) if isinstance(q, str)][:3]
        canonical_query_en = (data.get("canonical_query_en") or "").strip() or query.strip()

        keyword_queries, semantic_queries = _build_queries(
            canonical_query_en, intent, entities, conversation_history
        )

        spec = QuerySpec(
            intent=intent,
            entities=entities,
            constraints={},
            required_evidence=required_evidence,
            risk_level=risk_level,
            keyword_queries=keyword_queries,
            semantic_queries=semantic_queries,
            clarifying_questions=clarifying_questions,
            is_ambiguous=is_ambiguous,
            canonical_query_en=canonical_query_en if (source_lang and source_lang != "en") else None,
        )
        logger.info(
            "normalizer_llm",
            intent=intent,
            risk_level=risk_level,
            is_ambiguous=is_ambiguous,
            required_evidence=required_evidence,
            canonical_query_en=canonical_query_en[:100] if canonical_query_en and len(canonical_query_en) > 100 else canonical_query_en,
            translated=bool(source_lang and source_lang != "en"),
        )
        return spec
    except Exception as e:
        logger.warning("normalizer_llm_failed", error=str(e), query_preview=query[:80])
        return None


async def normalize(
    query: str,
    conversation_history: list[dict[str, str]] | None = None,
    locale: str | None = None,
    source_lang: str | None = None,
) -> QuerySpec:
    """Produce QuerySpec from raw query. Uses LLM when normalizer_use_llm=True, else rule-based."""
    q_stripped = query.strip()
    # Pre-retrieval gate: greetings/social need NO retrieval (fast path, no LLM)
    if SKIP_RETRIEVAL_PATTERN.match(q_stripped):
        logger.debug("normalizer_skip_retrieval", query_preview=q_stripped[:50])
        return QuerySpec(
            intent="social",
            entities=[],
            constraints={},
            required_evidence=[],
            risk_level="low",
            keyword_queries=[],
            semantic_queries=[],
            clarifying_questions=[],
            is_ambiguous=False,
            skip_retrieval=True,
            canned_response=DEFAULT_GREETING_RESPONSE,
        )

    settings = get_settings()
    use_llm = getattr(settings, "normalizer_use_llm", False)

    if use_llm:
        spec = await _normalize_llm(query, conversation_history, source_lang)
        if spec is not None:
            return spec
        logger.debug("normalizer_llm_fallback", reason="llm_failed_or_empty")

    return _normalize_rule_based(query, conversation_history)
