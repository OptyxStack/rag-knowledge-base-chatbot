"""Query Rewriter – LLM-based query rewriting for retrieval when QuerySpec is absent.

Phase 2.1: LLM service for keyword_query, semantic_query, retrieval_profile.
Phase 2.2: Redis cache by query_hash to reduce LLM calls.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.conversation_context import truncate_for_prompt
from app.services.llm_gateway import get_llm_gateway
from app.services.model_router import get_model_for_task

logger = get_logger(__name__)

QUERY_REWRITER_SYSTEM_PROMPT = """You are a query rewriter for a support RAG system.

Given a user query and optional conversation context, produce optimized queries for:
1. BM25 keyword search (exact-match terms, synonyms, domain vocabulary)
2. Vector/semantic search (natural phrasing)
3. Retrieval profile (routing hint)

Output JSON only, no markdown:
{
  "keyword_query": "optimized phrase for BM25 - include key terms, synonyms, domain terms",
  "semantic_query": "natural phrase for vector search - can be more conversational",
  "retrieval_profile": "pricing_profile|policy_profile|troubleshooting_profile|comparison_profile|account_profile|generic_profile"
}

Rules:
- keyword_query: favor exact-match terms (USD, pricing, VPS, refund, policy, etc.). Add synonyms if helpful.
- semantic_query: natural phrasing, can rephrase for clarity.
- retrieval_profile: pricing_profile for price/cost/plan/order; policy_profile for refund/terms/cancellation; troubleshooting_profile for how/setup/fix; comparison_profile for compare/vs; account_profile for account/login/billing; generic_profile otherwise.
- Use conversation context to resolve pronouns (e.g. "that" -> prior topic).
- If query is non-English, translate to English for both outputs."""


@dataclass
class QueryRewriteResult:
    """Result from LLM query rewriter."""

    keyword_query: str
    semantic_query: str
    retrieval_profile: str = "generic_profile"


def _cache_key(query: str, conversation_snippet: str, retry_boost: str) -> str:
    """Generate cache key for query rewrite."""
    payload = f"{query}|{conversation_snippet}|{retry_boost}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _conversation_snippet(conversation_history: list[dict[str, str]] | None) -> str:
    """Extract last N messages as string for cache key."""
    if not conversation_history or len(conversation_history) < 2:
        return ""
    settings = get_settings()
    max_chars = settings.conversation_snippet_max_chars
    content_limit = settings.conversation_message_content_max_chars
    parts = []
    for m in truncate_for_prompt(conversation_history):
        role = m.get("role", "user")
        content = (m.get("content") or "").strip()[:content_limit]
        if content:
            parts.append(f"{role}:{content}")
    return "|".join(parts)[:max_chars]


def _serialize_result(result: QueryRewriteResult) -> str:
    """Serialize to JSON. Safe for Redis (no pickle/RCE risk)."""
    return json.dumps({
        "keyword_query": result.keyword_query,
        "semantic_query": result.semantic_query,
        "retrieval_profile": result.retrieval_profile,
    })


def _deserialize_result(data: str) -> QueryRewriteResult | None:
    """Deserialize from JSON. Returns None on invalid data."""
    try:
        obj = json.loads(data)
        if not isinstance(obj, dict):
            return None
        return QueryRewriteResult(
            keyword_query=str(obj.get("keyword_query", "")),
            semantic_query=str(obj.get("semantic_query", "")),
            retrieval_profile=str(obj.get("retrieval_profile", "generic_profile")),
        )
    except (json.JSONDecodeError, TypeError):
        return None


async def _get_cached(key: str) -> QueryRewriteResult | None:
    """Get from Redis cache. Uses JSON (safe) not pickle."""
    settings = get_settings()
    if not getattr(settings, "query_rewriter_cache_enabled", False):
        return None
    try:
        import redis.asyncio as redis
        r = redis.from_url(settings.redis_url, decode_responses=True)
        data = await r.get(f"query_rewriter:{key}")
        await r.close()
        if data:
            return _deserialize_result(data)
    except Exception as e:
        logger.debug("query_rewriter_cache_get_failed", error=str(e))
    return None


async def _set_cached(key: str, result: QueryRewriteResult) -> None:
    """Set in Redis cache. Uses JSON (safe) not pickle."""
    settings = get_settings()
    if not getattr(settings, "query_rewriter_cache_enabled", False):
        return
    ttl = getattr(settings, "query_rewriter_cache_ttl_seconds", 3600)
    try:
        import redis.asyncio as redis
        r = redis.from_url(settings.redis_url, decode_responses=True)
        await r.setex(f"query_rewriter:{key}", ttl, _serialize_result(result))
        await r.close()
    except Exception as e:
        logger.debug("query_rewriter_cache_set_failed", error=str(e))


async def clear_cache() -> dict[str, int | bool]:
    """Clear query rewriter Redis cache namespace."""
    settings = get_settings()
    if not getattr(settings, "query_rewriter_cache_enabled", False):
        return {"enabled": False, "deleted_keys": 0}
    try:
        import redis.asyncio as redis

        r = redis.from_url(settings.redis_url, decode_responses=True)
        keys = await r.keys("query_rewriter:*")
        deleted = 0
        if keys:
            deleted = int(await r.delete(*keys))
        await r.close()
        logger.info("query_rewriter_cache_cleared", deleted_keys=deleted)
        return {"enabled": True, "deleted_keys": deleted}
    except Exception as e:
        logger.warning("query_rewriter_cache_clear_failed", error=str(e))
        return {"enabled": True, "deleted_keys": 0}


async def rewrite_for_retrieval(
    query: str,
    conversation_history: list[dict[str, str]] | None = None,
    retry_boost: str | None = None,
) -> QueryRewriteResult:
    """Rewrite query for retrieval using LLM. Uses cache when enabled.

    Called when QuerySpec is absent. Replaces rule-based heuristic expansion.
    """
    settings = get_settings()
    if not getattr(settings, "query_rewriter_use_llm", True):
        # Fallback: return query as-is (caller will use heuristic)
        return QueryRewriteResult(
            keyword_query=query.strip(),
            semantic_query=query.strip(),
            retrieval_profile="generic_profile",
        )

    conv_snippet = _conversation_snippet(conversation_history)
    boost = retry_boost or ""
    cache_key = _cache_key(query.strip(), conv_snippet, boost)

    cached = await _get_cached(cache_key)
    if cached:
        logger.debug("query_rewriter_cache_hit", query_hash=cache_key[:16])
        return cached

    content_limit = get_settings().conversation_message_content_max_chars
    user_parts = [f"Query: {query.strip()}"]
    if conversation_history and len(conversation_history) >= 2:
        ctx = "\n".join(
            f"{m.get('role', 'user')}: {(m.get('content') or '')[:content_limit]}"
            for m in truncate_for_prompt(conversation_history)
        )
        user_parts.append(f"Conversation context:\n{ctx}")
    if retry_boost:
        user_parts.append(f"Retry boost terms to include: {retry_boost}")

    user_content = "\n\n".join(user_parts)

    try:
        model = get_model_for_task("query_rewriter")
        llm = get_llm_gateway()
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": QUERY_REWRITER_SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            model=model,
            max_tokens=256,
        )
        content = (resp.content or "").strip()
        if "```json" in content:
            match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
            content = match.group(1) if match else content
        elif "```" in content:
            match = re.search(r"```\s*([\s\S]*?)\s*```", content)
            content = match.group(1) if match else content

        data = json.loads(content)
        keyword = (data.get("keyword_query") or "").strip() or query.strip()
        semantic = (data.get("semantic_query") or "").strip() or query.strip()
        profile = (data.get("retrieval_profile") or "generic_profile").strip().lower()
        valid_profiles = {
            "pricing_profile", "policy_profile", "troubleshooting_profile",
            "comparison_profile", "account_profile", "generic_profile",
        }
        if profile not in valid_profiles:
            profile = "generic_profile"

        if retry_boost:
            keyword = f"{keyword} {retry_boost}".strip()

        result = QueryRewriteResult(
            keyword_query=keyword,
            semantic_query=semantic,
            retrieval_profile=profile,
        )
        await _set_cached(cache_key, result)
        return result

    except Exception as e:
        logger.warning("query_rewriter_llm_failed", error=str(e))
        return QueryRewriteResult(
            keyword_query=query.strip(),
            semantic_query=query.strip(),
            retrieval_profile="generic_profile",
        )
