"""
Request Normalizer — flexible, LLM-led.

Goals:
- All queries go through LLM.
- Minimal invariants: JSON shape + light type coercion.
- No rule-based keyword matching / regex intent detection / slot heuristics.
- Fallback only when LLM fails (keeps pipeline alive).

Notes:
- We treat required_evidence as SOFT by default (hard_requirements=[]), to keep the system flexible.
"""

from __future__ import annotations

import json
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.llm_gateway import get_llm_gateway
from app.services.schemas import QuerySpec

logger = get_logger(__name__)


_ALLOWED_INTENTS = {
    "transactional",
    "comparison",
    "policy",
    "troubleshooting",
    "account",
    "informational",
    "ambiguous",
    "social",
}
_ALLOWED_RISK = {"low", "medium", "high"}

NORMALIZER_SYSTEM_PROMPT = """You normalize a user's query for a support chatbot.

Return JSON ONLY (no markdown, no extra text). If unsure, use empty lists or null.

Schema:
{
  "canonical_query_en": "English translation or original if already English",
  "entities": ["..."],

  "required_evidence": ["..."],
  "risk_level": "low|medium|high",

  "is_ambiguous": false,
  "clarifying_questions": [],

  "keyword_queries": ["..."],
  "semantic_queries": ["..."],
  "retrieval_rewrites": ["..."],

  "skip_retrieval": false,
  "canned_response": "optional when skip_retrieval is true",

  "product_type": null,
  "os": null,
  "comparison_targets": [],
  "billing_cycle": null
}

Guidance (non-binding):
- canonical_query_en must be in English (translate if needed). Use conversation context to resolve referents (e.g. "for my window rdp" after "change the default port for" → "How do I change the default port for my Windows RDP?").
- is_ambiguous: false when the user's message (with context) provides the missing referent. Example: assistant asked "What application?" and user replied "for my Windows RDP" → referent is clear, is_ambiguous=false. Set is_ambiguous=true only when the referent remains unclear.
- clarifying_questions: empty when is_ambiguous=false. When is_ambiguous=true, list 1–3 specific questions.
- skip_retrieval: true when the query is routine and needs no knowledge base (greeting, thanks, bye, simple chitchat). Respond immediately with canned_response.
- canned_response: when skip_retrieval is true, provide a friendly reply (e.g. greeting "Hello! How can I help you today?").
- keyword_queries / semantic_queries: focus on the resolved question (1–2 each). Empty when skip_retrieval.
- retrieval_rewrites: 0–5 short variations for retry. Empty when skip_retrieval.
"""


def _get_greeting_response() -> str:
    app_name = (get_settings().app_name or "").strip()
    if app_name:
        return f"Hello! Welcome to {app_name} support. How can I help you today?"
    return "Hello! Welcome. How can I help you today?"


def _extract_probable_json(text: str) -> str:
    """
    Robust-ish JSON extraction without content rules:
    - Accept raw JSON.
    - If code-fenced, strip fences.
    - Else try to isolate the first {...} block.
    """
    s = (text or "").strip()

    if s.startswith("```"):
        first_nl = s.find("\n")
        if first_nl != -1:
            s = s[first_nl + 1 :]
        last = s.rfind("```")
        if last != -1:
            s = s[:last].strip()

    if (s.startswith("{") and s.endswith("}")) or (s.startswith("[") and s.endswith("]")):
        return s

    start = s.find("{")
    end = s.rfind("}")
    if 0 <= start < end:
        return s[start : end + 1].strip()

    return s


def _as_str(v: Any, default: str = "") -> str:
    try:
        return str(v).strip()
    except Exception:
        return default


def _as_bool(v: Any, default: bool = False) -> bool:
    if isinstance(v, bool):
        return v
    return default


def _as_str_list(v: Any, limit: int | None = None) -> list[str]:
    if not v:
        return []
    if isinstance(v, list):
        out = [str(x).strip() for x in v if x is not None and str(x).strip()]
    else:
        out = [str(v).strip()] if str(v).strip() else []
    if limit is not None:
        out = out[:limit]
    # stable de-dup
    seen: set[str] = set()
    dedup: list[str] = []
    for x in out:
        xl = x.lower()
        if xl in seen:
            continue
        seen.add(xl)
        dedup.append(x)
    return dedup


def _sanitize_intent(v: Any) -> str:
    intent = _as_str(v, "informational").lower()
    return intent if intent in _ALLOWED_INTENTS else "informational"


def _sanitize_risk(v: Any) -> str:
    risk = _as_str(v, "low").lower()
    return risk if risk in _ALLOWED_RISK else "low"


def _parse_llm_slots(data: dict[str, Any]) -> dict[str, Any]:
    """
    Only take what the LLM explicitly provides.
    No config-driven / rule-based extraction.
    """
    slots: dict[str, Any] = {}

    pt = _as_str(data.get("product_type"))
    if pt:
        slots["product_type"] = pt.lower()

    os_val = _as_str(data.get("os"))
    if os_val:
        slots["os"] = os_val.lower()

    bc = _as_str(data.get("billing_cycle")).lower()
    if bc in ("monthly", "yearly"):
        slots["billing_cycle"] = bc

    ct = data.get("comparison_targets")
    if isinstance(ct, list):
        targets = [str(t).strip().lower() for t in ct if t and str(t).strip()]
        targets = targets[:3]
        if len(targets) >= 2:
            slots["comparison_targets"] = targets

    return slots


def _build_rewrite_candidates(
    query: str,
    canonical_query_en: str,
    keyword_queries: list[str],
    semantic_queries: list[str],
    retrieval_rewrites: list[str],
) -> list[str]:
    candidates: list[str] = []
    for s in [query, canonical_query_en, *retrieval_rewrites, *keyword_queries, *semantic_queries]:
        s2 = (s or "").strip()
        if s2:
            candidates.append(s2)
    # stable de-dup
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        cl = c.lower()
        if cl in seen:
            continue
        seen.add(cl)
        out.append(c)
    return out[:12]


async def _normalize_llm(
    query: str,
    conversation_history: list[dict[str, str]] | None,
    source_lang: str | None = None,
    locale: str | None = None,
) -> QuerySpec | None:
    from app.services.model_router import get_model_for_task

    model = get_model_for_task("normalizer")

    user_parts = [f"Query: {query.strip()}"]
    if source_lang:
        user_parts.append(f"source_lang: {source_lang}")
    if locale:
        user_parts.append(f"locale: {locale}")

    # Provide lightweight context. No rewriting/expansion logic in code.
    if conversation_history:
        ctx = "\n".join(
            f"{m.get('role', 'user')}: {(m.get('content') or '')[:240]}"
            for m in conversation_history[-4:]
        ).strip()
        if ctx:
            user_parts.append(f"Conversation context (last 4):\n{ctx}")

    user_content = "\n\n".join(user_parts).strip()

    try:
        from app.core.tracing import current_llm_task_var

        current_llm_task_var.set("normalizer")
        llm = get_llm_gateway()
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": NORMALIZER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            model=model,
            max_tokens=512,
        )

        raw = (resp.content or "").strip()
        payload = json.loads(_extract_probable_json(raw))
        if not isinstance(payload, dict):
            raise ValueError("LLM output is not a JSON object")

        risk_level = _sanitize_risk(payload.get("risk_level"))

        canonical_query_en = _as_str(payload.get("canonical_query_en")) or query.strip()
        src_lang = (_as_str(source_lang) or "en").lower() or "en"
        translation_needed = bool(src_lang != "en" and canonical_query_en.strip() and canonical_query_en.strip() != query.strip())

        entities = _as_str_list(payload.get("entities"), limit=12)
        required_evidence = _as_str_list(payload.get("required_evidence"), limit=10)

        is_ambiguous = _as_bool(payload.get("is_ambiguous"), False)
        clarifying_questions = _as_str_list(payload.get("clarifying_questions"), limit=3)

        keyword_queries = _as_str_list(payload.get("keyword_queries"), limit=2)
        semantic_queries = _as_str_list(payload.get("semantic_queries"), limit=2)
        retrieval_rewrites = _as_str_list(payload.get("retrieval_rewrites"), limit=5)

        skip_retrieval = _as_bool(payload.get("skip_retrieval"), False)
        canned_response = _as_str(payload.get("canned_response"))
        if skip_retrieval and not canned_response:
            canned_response = _get_greeting_response()

        # Minimal defaults if LLM omits retrieval phrases
        if not keyword_queries and not skip_retrieval:
            keyword_queries = [canonical_query_en.strip() or query.strip()]
        if not semantic_queries and not skip_retrieval:
            semantic_queries = [canonical_query_en.strip() or query.strip()]

        slots = _parse_llm_slots(payload)
        constraints = dict(slots) if slots else {}

        rewrite_candidates = _build_rewrite_candidates(
            query=query.strip(),
            canonical_query_en=canonical_query_en.strip(),
            keyword_queries=keyword_queries,
            semantic_queries=semantic_queries,
            retrieval_rewrites=retrieval_rewrites,
        )

        # Keep these flexible: treat required_evidence as soft, not hard.
        hard_requirements: list[str] = []
        soft_requirements: list[str] = list(required_evidence)

        answer_mode_hint = "ask_user" if is_ambiguous else "strong"

        intent = "social" if skip_retrieval else _sanitize_intent(payload.get("intent"))
        spec = QuerySpec(
            intent=intent,
            entities=entities,
            constraints=constraints,
            required_evidence=required_evidence,
            risk_level=risk_level,
            keyword_queries=([] if skip_retrieval else keyword_queries),
            semantic_queries=([] if skip_retrieval else semantic_queries),
            clarifying_questions=clarifying_questions,
            is_ambiguous=is_ambiguous,
            skip_retrieval=skip_retrieval,
            canned_response=(canned_response if skip_retrieval else None),
            original_query=query.strip(),
            source_lang=src_lang,
            translation_needed=translation_needed,
            canonical_query_en=(canonical_query_en.strip() if translation_needed else None),
            user_goal="general_info" if skip_retrieval else intent,
            resolved_slots=slots,
            missing_slots=[],
            ambiguity_type=("referential" if is_ambiguous else None),
            answerable_without_clarification=not is_ambiguous,
            hard_requirements=hard_requirements,
            soft_requirements=soft_requirements,
            retrieval_profile="generic_profile",
            rewrite_candidates=([] if skip_retrieval else rewrite_candidates),
            answer_mode_hint=answer_mode_hint,
            extraction_mode="llm_primary",
            config_overrides_applied=[],
        )

        logger.info(
            "normalizer_llm",
            intent=spec.intent,
            risk_level=spec.risk_level,
            is_ambiguous=spec.is_ambiguous,
            skip_retrieval=spec.skip_retrieval,
            required_evidence=spec.required_evidence,
            translated=spec.translation_needed,
            canonical_query_preview=(canonical_query_en[:120] if canonical_query_en else None),
        )
        return spec

    except Exception as e:
        logger.warning("normalizer_llm_failed", error=str(e), query_preview=(query or "")[:80])
        return None


def _build_minimal_fallback(query: str, source_lang: str | None = None) -> QuerySpec:
    """Minimal QuerySpec when LLM fails. Keeps the pipeline running."""
    q = (query or "").strip()
    lang = (_as_str(source_lang) or "en").lower() or "en"
    return QuerySpec(
        intent="informational",
        entities=[],
        constraints={},
        required_evidence=[],
        risk_level="low",
        keyword_queries=[q] if q else [],
        semantic_queries=[q] if q else [],
        clarifying_questions=[],
        is_ambiguous=False,
        skip_retrieval=False,
        canned_response=None,
        original_query=q,
        source_lang=lang,
        translation_needed=False,
        canonical_query_en=None,
        user_goal="general_info",
        resolved_slots={},
        missing_slots=[],
        ambiguity_type=None,
        answerable_without_clarification=True,
        hard_requirements=[],
        soft_requirements=[],
        retrieval_profile="generic_profile",
        rewrite_candidates=[q] if q else [],
        answer_mode_hint="strong",
        extraction_mode="llm_fallback",
        config_overrides_applied=[],
    )


async def normalize(
    query: str,
    conversation_history: list[dict[str, str]] | None = None,
    locale: str | None = None,
    source_lang: str | None = None,
) -> QuerySpec:
    """Produce QuerySpec from raw query. LLM-led; minimal fallback on error."""
    q = (query or "").strip()
    spec = await _normalize_llm(q, conversation_history, source_lang=source_lang, locale=locale)
    if spec is not None:
        return spec
    logger.warning("normalizer_llm_fallback", reason="llm_failed", query_preview=q[:80])
    return _build_minimal_fallback(q, source_lang)