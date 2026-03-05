"""Retrieval Planner – Workstream 3: Choose RetrievalPlan from QuerySpec.

Per UPGRADE_RAG_DESIGN Phase 2/3:
- Intent classification is no longer re-derived inside retrieval
- Each retry produces a materially different RetrievalPlan
- Plan encodes why the current attempt exists for evaluation
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from app.core.config import get_settings
from app.services.schemas import QuerySpec, RetrievalPlan

if TYPE_CHECKING:
    from app.services.retry_planner import RetryStrategy


def _resolve_profile(query: str, query_spec: QuerySpec | None) -> str:
    """Resolve retrieval profile. QuerySpec preferred, else keyword fallback."""
    if query_spec and getattr(query_spec, "retrieval_profile", None):
        return query_spec.retrieval_profile
    q = query.lower()
    if any(kw in q for kw in ["price", "cost", "pricing", "order", "buy", "subscribe", "link"]):
        return "pricing_profile"
    if any(kw in q for kw in ["refund", "policy", "terms", "cancellation"]):
        return "policy_profile"
    if any(kw in q for kw in ["how", "setup", "install", "fix", "error", "step"]):
        return "troubleshooting_profile"
    if any(kw in q for kw in ["compare", "diff", "difference", "vs", "versus"]):
        return "comparison_profile"
    if any(kw in q for kw in ["account", "login", "billing"]):
        return "account_profile"
    return "generic_profile"


def _resolve_queries(
    query: str,
    query_spec: QuerySpec | None,
    retry_strategy: "RetryStrategy | None",
) -> tuple[str, str, list[str]]:
    """Return (keyword_query, semantic_query, fallback_queries)."""
    keyword = query
    semantic = query
    fallbacks: list[str] = []

    if query_spec:
        keyword = (
            query_spec.keyword_queries[0]
            if query_spec.keyword_queries
            else getattr(query_spec, "rewrite_candidates", [None])[0] or query
        )
        semantic = (
            query_spec.semantic_queries[0]
            if query_spec.semantic_queries
            else getattr(query_spec, "rewrite_candidates", [None])[0] or query
        )
        fallbacks = list(getattr(query_spec, "rewrite_candidates", None) or [])[1:5]

    if retry_strategy and retry_strategy.suggested_query:
        keyword = retry_strategy.suggested_query
        semantic = retry_strategy.suggested_query
        fallbacks = []
    elif retry_strategy and retry_strategy.boost_patterns:
        boost = " ".join(p for p in retry_strategy.boost_patterns if not p.startswith("\\"))[:100]
        if boost:
            keyword = f"{keyword} {boost}".strip()
            semantic = semantic  # keep semantic unchanged for vector

    return keyword, semantic, fallbacks


def _derive_doc_types(
    profile: str,
    query_spec: QuerySpec | None,
    is_pricing: bool,
    settings,
    retry_strategy: "RetryStrategy | None" = None,
) -> tuple[list[str], list[str]]:
    """Return (preferred_doc_types, excluded_doc_types)."""
    preferred: list[str] = []
    excluded: list[str] = []

    hard = set()
    if query_spec:
        hard = {
            str(x)
            for x in (getattr(query_spec, "hard_requirements", None) or (query_spec.required_evidence or []))
            if isinstance(x, str)
        }

    if is_pricing and settings.retrieval_plans_fetch_doc_types:
        preferred.extend(
            t.strip()
            for t in settings.retrieval_plans_fetch_doc_types.split(",")
            if t.strip()
        )
    if profile == "policy_profile" or "policy_language" in hard:
        policy_types = [
            t.strip()
            for t in (settings.retrieval_policy_doc_types or "").split(",")
            if t.strip()
        ]
        if policy_types:
            preferred.extend(policy_types)
    if profile == "troubleshooting_profile" or "steps_structure" in hard:
        preferred.extend(["howto", "docs", "faq"])
    if "has_any_url" in hard:
        preferred.append("faq")

    if retry_strategy and retry_strategy.filter_doc_types:
        preferred = list(dict.fromkeys(retry_strategy.filter_doc_types + preferred))

    return list(dict.fromkeys(preferred)), excluded


def build_retrieval_plan(
    query: str,
    attempt: int,
    query_spec: QuerySpec | None = None,
    retry_strategy: "RetryStrategy | None" = None,
) -> RetrievalPlan:
    """Build RetrievalPlan from QuerySpec and retry context.

    Attempt 1: broad hybrid, profile-driven.
    Attempt 2: precision targeted by retry_strategy (boost, filter, exclude).
    """
    settings = get_settings()
    profile = _resolve_profile(query, query_spec)
    keyword, semantic, fallbacks = _resolve_queries(query, query_spec, retry_strategy)

    is_pricing = (
        profile == "pricing_profile"
        or (
            profile == "comparison_profile"
            and any(kw in query.lower() for kw in ["price", "pricing", "cost", "plan", "offer", "link"])
        )
        or (
            query_spec
            and (
                set(getattr(query_spec, "hard_requirements", None) or query_spec.required_evidence or [])
                & {"transaction_link"}
            )
        )
    )

    preferred_doc_types, excluded_doc_types = _derive_doc_types(
        profile, query_spec, is_pricing, settings, retry_strategy
    )

    fetch_n = settings.retrieval_top_n
    rerank_k = settings.retrieval_top_k
    if is_pricing:
        fetch_n = min(fetch_n * 2, 100)
        rerank_k = min(rerank_k + settings.retrieval_plans_extra_chunks, 24)
    elif profile == "policy_profile":
        fetch_n = min(fetch_n + max(6, fetch_n // 2), 100)
        rerank_k = min(rerank_k + 2, 24)
    elif profile == "troubleshooting_profile":
        fetch_n = min(fetch_n + max(4, fetch_n // 3), 100)
        rerank_k = min(rerank_k + 2, 24)

    reason = "broad_hybrid" if attempt == 1 else "retry_precision"
    if retry_strategy and retry_strategy.suggested_query:
        reason = "evidence_evaluator_suggested"
    elif retry_strategy and retry_strategy.boost_patterns:
        reason = "retry_boost_patterns"

    boost_patterns = list(retry_strategy.boost_patterns) if retry_strategy else []
    exclude_patterns = list(retry_strategy.exclude_patterns) if retry_strategy else []

    return RetrievalPlan(
        profile=profile,
        attempt_index=attempt,
        reason=reason,
        query_keyword=keyword,
        query_semantic=semantic,
        preferred_doc_types=preferred_doc_types or None,
        excluded_doc_types=excluded_doc_types or None,
        preferred_sources=None,
        fallback_queries=fallbacks[:3] if fallbacks else None,
        bm25_weight=1.0,
        vector_weight=1.0,
        rerank_weight=1.0,
        fetch_n=fetch_n,
        rerank_k=rerank_k,
        enable_parent_expansion=bool(retry_strategy and retry_strategy.context_expansion),
        enable_neighbor_expansion=bool(retry_strategy and retry_strategy.context_expansion),
        enable_exact_slot_fetch=False,
        boost_patterns=boost_patterns or None,
        exclude_patterns=exclude_patterns or None,
        budget_hint=None,
    )
