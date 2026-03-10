"""Retrieval planning.

Single source of truth for:
- query rewrite selection per attempt
- retrieval profile/doc type/hard requirement policy
- RetrievalPlan construction

When QuerySpec is present, its retrieval fields are authoritative.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.core.config import get_settings
from app.services.doc_type_service import get_valid_doc_type_keys
from app.services.schemas import HypothesisSpec, QuerySpec, RetrievalPlan

if TYPE_CHECKING:
    from app.services.retry_planner import RetryStrategy


_ALLOWED_RETRIEVAL_PROFILES = {
    "pricing_profile",
    "policy_profile",
    "troubleshooting_profile",
    "comparison_profile",
    "account_profile",
    "generic_profile",
}
_AUTHORITATIVE_DOC_TYPES = {"pricing", "policy", "tos", "docs", "howto"}
_SUPPORTING_DOC_TYPES = {"conversation", "faq", "blog"}
_EVIDENCE_FAMILY_PROFILE_MAP = {
    "policy_terms": "policy_profile",
    "pricing_limits": "pricing_profile",
    "transactional_link": "pricing_profile",
    "troubleshooting_steps": "troubleshooting_profile",
    "comparison_analysis": "comparison_profile",
    "account_access": "account_profile",
    "capability_availability": "generic_profile",
    "general_info": "generic_profile",
}


def _normalize_str_list(values: list[Any] | None) -> list[str]:
    out: list[str] = []
    for item in values or []:
        text = str(item).strip()
        if text:
            out.append(text)
    return out


def _merge_unique(values: list[str] | None) -> list[str]:
    return list(dict.fromkeys(_normalize_str_list(values)))


def sanitize_retrieval_profile(value: Any) -> str | None:
    """Validate retrieval profile label."""
    profile = str(value or "").strip().lower()
    if not profile:
        return None
    if profile in _ALLOWED_RETRIEVAL_PROFILES:
        return profile
    return None


def _valid_doc_types() -> set[str]:
    valid = {str(x).strip().lower() for x in get_valid_doc_type_keys() if str(x).strip()}
    if not valid:
        valid = {"pricing", "policy", "tos", "faq", "howto", "docs", "conversation", "blog"}
    return valid


def _sanitize_doc_type_list(values: list[Any] | None) -> list[str]:
    valid = _valid_doc_types()
    out: list[str] = []
    for item in values or []:
        text = str(item).strip().lower()
        if text and text in valid and text not in out:
            out.append(text)
    return out


def derive_hard_requirements(
    explicit_hard: list[str],
    required_evidence: list[str],
    risk_level: str,
) -> list[str]:
    """Derive hard requirements from QuerySpec fields when explicit list is missing."""
    explicit = _normalize_str_list(explicit_hard)
    required = _normalize_str_list(required_evidence)
    if explicit:
        return explicit
    if not required:
        return []
    if str(risk_level).strip().lower() in {"medium", "high"}:
        return list(dict.fromkeys(required))
    strong = {"policy_language", "transaction_link", "steps_structure"}
    return [item for item in required if item in strong]


def infer_retrieval_profile(
    intent: str,
    required_evidence: list[str],
    hard_requirements: list[str],
    evidence_families: list[str] | None = None,
) -> str:
    """Infer retrieval profile when normalizer output omits it."""
    req = {x.lower() for x in _normalize_str_list(required_evidence)}
    hard = {x.lower() for x in _normalize_str_list(hard_requirements)}
    combined = req | hard
    for family in _normalize_str_list(evidence_families):
        mapped = _EVIDENCE_FAMILY_PROFILE_MAP.get(family.lower())
        if mapped:
            return mapped
    intent_norm = str(intent or "").strip().lower()
    if intent_norm == "policy" or "policy_language" in combined:
        return "policy_profile"
    if intent_norm == "troubleshooting" or "steps_structure" in combined:
        return "troubleshooting_profile"
    if intent_norm == "comparison":
        return "comparison_profile"
    if intent_norm == "account":
        return "account_profile"
    if intent_norm == "transactional" or {"numbers_units", "transaction_link", "has_any_url"} & combined:
        return "pricing_profile"
    return "generic_profile"


def collect_rewrite_candidates(
    base_query: str,
    query_spec: QuerySpec | None,
) -> list[str]:
    """Collect deduplicated rewrite candidates from QuerySpec."""
    candidates = [base_query.strip()]
    if query_spec and getattr(query_spec, "rewrite_candidates", None):
        candidates.extend(
            str(candidate).strip()
            for candidate in (query_spec.rewrite_candidates or [])
            if isinstance(candidate, str) and candidate.strip()
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def resolve_retrieval_query(
    *,
    base_query: str,
    attempt: int,
    query_spec: QuerySpec | None,
    retry_strategy: RetryStrategy | None,
    explicit_override: str | None = None,
) -> tuple[str, str, list[str]]:
    """Resolve retrieval query for this attempt from QuerySpec/retry inputs."""
    rewrite_candidates = collect_rewrite_candidates(base_query, query_spec)

    if retry_strategy and getattr(retry_strategy, "suggested_query", None):
        suggested = str(retry_strategy.suggested_query).strip()
        if suggested:
            return suggested, "retry_strategy_suggested_query", rewrite_candidates

    if explicit_override and explicit_override.strip():
        return explicit_override.strip(), "explicit_retry_query", rewrite_candidates

    if attempt > 1 and len(rewrite_candidates) > 1:
        idx = min(attempt - 1, len(rewrite_candidates) - 1)
        candidate = rewrite_candidates[idx].strip()
        if candidate:
            return candidate, f"rewrite_candidate_{idx}", rewrite_candidates

    return base_query.strip(), "base_query", rewrite_candidates


def _resolve_profile(query_spec: QuerySpec | None, fallback_profile: str | None = None) -> str:
    """Resolve retrieval profile from authoritative QuerySpec when present."""
    if query_spec is not None:
        profile = str(getattr(query_spec, "retrieval_profile", "")).strip()
        return profile or "generic_profile"
    if fallback_profile:
        profile = str(fallback_profile).strip()
        if profile:
            return profile
    return "generic_profile"


def _resolve_hard_requirements(query_spec: QuerySpec | None) -> list[str]:
    """QuerySpec is authoritative for hard requirements."""
    if not query_spec:
        return []
    return _normalize_str_list(getattr(query_spec, "hard_requirements", None) or [])


def _resolve_doc_type_prior(query_spec: QuerySpec | None) -> list[str]:
    """QuerySpec is authoritative for doc_type_prior when present."""
    if not query_spec:
        return []
    return _sanitize_doc_type_list(getattr(query_spec, "doc_type_prior", None) or [])


def _resolve_active_hypothesis(
    query_spec: QuerySpec | None,
    retry_strategy: RetryStrategy | None = None,
) -> HypothesisSpec | None:
    if not query_spec:
        return None
    hypotheses: list[HypothesisSpec] = []
    if getattr(query_spec, "primary_hypothesis", None):
        hypotheses.append(query_spec.primary_hypothesis)
    hypotheses.extend(list(getattr(query_spec, "fallback_hypotheses", None) or []))
    if not hypotheses:
        return None
    if retry_strategy and getattr(retry_strategy, "hypothesis_index", None) is not None:
        idx = int(retry_strategy.hypothesis_index)
        if 0 <= idx < len(hypotheses):
            return hypotheses[idx]
    if retry_strategy and getattr(retry_strategy, "hypothesis_name", None):
        name = str(retry_strategy.hypothesis_name).strip().lower()
        for hypothesis in hypotheses:
            if (hypothesis.name or "").strip().lower() == name:
                return hypothesis
    return hypotheses[0]


def _derive_preferred_sources(
    *,
    query_spec: QuerySpec | None,
    active_hypothesis: HypothesisSpec | None = None,
    retry_strategy: RetryStrategy | None = None,
) -> list[str]:
    sources: list[str] = []
    if retry_strategy and getattr(retry_strategy, "preferred_sources_override", None):
        sources.extend(_normalize_str_list(retry_strategy.preferred_sources_override))
    if active_hypothesis and getattr(active_hypothesis, "preferred_sources", None):
        sources.extend(_normalize_str_list(active_hypothesis.preferred_sources))
    if query_spec and getattr(query_spec, "doc_type_prior", None):
        doc_types = {d.lower() for d in _resolve_doc_type_prior(query_spec)}
        if "conversation" in doc_types:
            sources.append("conversation")
    if retry_strategy and retry_strategy.filter_doc_types:
        retry_doc_types = {d.lower() for d in _normalize_str_list(retry_strategy.filter_doc_types)}
        if "conversation" in retry_doc_types:
            sources.append("conversation")
    if "conversation" not in {s.lower() for s in sources}:
        sources.append("conversation")
    return _merge_unique(sources)


def _derive_lane_doc_types(
    *,
    active_hypothesis: HypothesisSpec | None,
    preferred_doc_types: list[str],
) -> tuple[list[str], list[str]]:
    all_doc_types = _sanitize_doc_type_list(
        (active_hypothesis.doc_type_prior if active_hypothesis and active_hypothesis.doc_type_prior else preferred_doc_types)
    )
    authoritative = [d for d in all_doc_types if d in _AUTHORITATIVE_DOC_TYPES]
    supporting = [d for d in all_doc_types if d in _SUPPORTING_DOC_TYPES]
    if not authoritative:
        authoritative = [d for d in preferred_doc_types if d in _AUTHORITATIVE_DOC_TYPES]
    if not supporting and "conversation" in _valid_doc_types():
        supporting = ["conversation"]
    return list(dict.fromkeys(authoritative)), list(dict.fromkeys(supporting))


def _resolve_queries_from_query_spec(
    selected_query: str,
    query_source: str,
    query_spec: QuerySpec,
    retry_strategy: RetryStrategy | None,
) -> tuple[str, str, list[str]]:
    keyword = (
        query_spec.keyword_queries[0]
        if query_spec.keyword_queries
        else selected_query
    )
    semantic = (
        query_spec.semantic_queries[0]
        if query_spec.semantic_queries
        else selected_query
    )
    fallbacks = list((query_spec.rewrite_candidates or [])[1:5])

    if query_source != "base_query":
        keyword = selected_query
        semantic = selected_query

    if retry_strategy and retry_strategy.boost_patterns:
        boost = " ".join(
            p for p in (retry_strategy.boost_patterns or []) if not str(p).startswith("\\")
        )[:100]
        if boost:
            keyword = f"{keyword} {boost}".strip()

    return keyword.strip(), semantic.strip(), fallbacks


def _derive_doc_types(
    *,
    profile: str,
    query_spec: QuerySpec | None,
    active_hypothesis: HypothesisSpec | None = None,
    hard_requirements: list[str],
    is_pricing: bool,
    settings,
    retry_strategy: RetryStrategy | None = None,
) -> tuple[list[str], list[str]]:
    preferred: list[str] = []
    excluded: list[str] = []

    if active_hypothesis and getattr(active_hypothesis, "doc_type_prior", None):
        preferred.extend(_sanitize_doc_type_list(active_hypothesis.doc_type_prior))
    elif query_spec is not None:
        preferred.extend(_resolve_doc_type_prior(query_spec))
    else:
        hard = {h.lower() for h in hard_requirements}
        if is_pricing and settings.retrieval_plans_fetch_doc_types:
            preferred.extend(
                t.strip()
                for t in settings.retrieval_plans_fetch_doc_types.split(",")
                if t.strip()
            )
        if profile == "policy_profile" or "policy_language" in hard:
            preferred.extend(
                t.strip()
                for t in (settings.retrieval_policy_doc_types or "").split(",")
                if t.strip()
            )
        if profile == "troubleshooting_profile" or "steps_structure" in hard:
            preferred.extend(["howto", "docs", "faq"])

    if retry_strategy and retry_strategy.filter_doc_types:
        retry_doc_types = _normalize_str_list(retry_strategy.filter_doc_types)
        preferred = list(dict.fromkeys(retry_doc_types + preferred))

    return list(dict.fromkeys(preferred)), excluded


def _build_plan_from_inputs(
    *,
    query: str,
    attempt: int,
    profile: str,
    query_keyword: str,
    query_semantic: str,
    fallback_queries: list[str],
    hard_requirements: list[str],
    preferred_doc_types: list[str],
    excluded_doc_types: list[str],
    preferred_sources: list[str],
    active_hypothesis: HypothesisSpec | None,
    retry_strategy: RetryStrategy | None = None,
) -> RetrievalPlan:
    settings = get_settings()
    is_pricing = profile == "pricing_profile"

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
    authoritative_doc_types, supporting_doc_types = _derive_lane_doc_types(
        active_hypothesis=active_hypothesis,
        preferred_doc_types=preferred_doc_types,
    )
    active_required = _normalize_str_list(
        (
            retry_strategy.required_evidence_override
            if retry_strategy and getattr(retry_strategy, "required_evidence_override", None) is not None
            else (
                active_hypothesis.required_evidence
                if active_hypothesis and active_hypothesis.required_evidence
                else []
            )
        )
    )
    active_hard = _normalize_str_list(
        (
            retry_strategy.hard_requirements_override
            if retry_strategy and getattr(retry_strategy, "hard_requirements_override", None) is not None
            else (
                active_hypothesis.hard_requirements
                if active_hypothesis and active_hypothesis.hard_requirements
                else hard_requirements
            )
        )
    )
    active_soft = _normalize_str_list(
        (
            retry_strategy.soft_requirements_override
            if retry_strategy and getattr(retry_strategy, "soft_requirements_override", None) is not None
            else (
                active_hypothesis.soft_requirements
                if active_hypothesis and active_hypothesis.soft_requirements
                else []
            )
        )
    )

    return RetrievalPlan(
        profile=profile,
        attempt_index=attempt,
        reason=reason,
        query_keyword=query_keyword,
        query_semantic=query_semantic,
        active_hypothesis_name=(active_hypothesis.name if active_hypothesis else "primary"),
        evidence_families=list(getattr(active_hypothesis, "evidence_families", None) or []),
        answer_shape=(
            str(getattr(retry_strategy, "answer_shape_override", "") or "").strip()
            or str(getattr(active_hypothesis, "answer_shape", "direct_lookup") or "direct_lookup")
        ),
        active_required_evidence=active_required,
        active_hard_requirements=active_hard,
        active_soft_requirements=active_soft,
        preferred_doc_types=preferred_doc_types or None,
        excluded_doc_types=excluded_doc_types or None,
        preferred_sources=preferred_sources or None,
        authoritative_doc_types=authoritative_doc_types or None,
        supporting_doc_types=supporting_doc_types or None,
        fallback_queries=fallback_queries[:3] if fallback_queries else None,
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
        budget_hint={
            "boost_pricing": is_pricing,
            "ensure_doc_types": preferred_doc_types,
            "preferred_sources": preferred_sources,
            "hard_requirements": hard_requirements,
            "active_required_evidence": active_required,
            "active_hard_requirements": active_hard,
        },
    )


def build_retrieval_plan(
    query: str,
    attempt: int,
    query_spec: QuerySpec | None = None,
    retry_strategy: RetryStrategy | None = None,
) -> RetrievalPlan:
    """Sync planner (used by existing tests/callers)."""
    selected_query, query_source, rewrite_candidates = resolve_retrieval_query(
        base_query=query,
        attempt=attempt,
        query_spec=query_spec,
        retry_strategy=retry_strategy,
        explicit_override=None,
    )

    hard_requirements = _resolve_hard_requirements(query_spec)
    active_hypothesis = _resolve_active_hypothesis(query_spec, retry_strategy=retry_strategy)
    profile = (
        sanitize_retrieval_profile(
            getattr(retry_strategy, "retrieval_profile_override", None)
            or getattr(active_hypothesis, "retrieval_profile", "")
        )
        if (retry_strategy or active_hypothesis)
        else None
    ) or _resolve_profile(query_spec)

    if query_spec:
        keyword, semantic, fallback_queries = _resolve_queries_from_query_spec(
            selected_query=selected_query,
            query_source=query_source,
            query_spec=query_spec,
            retry_strategy=retry_strategy,
        )
    else:
        keyword = selected_query
        semantic = selected_query
        fallback_queries = rewrite_candidates[1:5]

    preferred_doc_types, excluded_doc_types = _derive_doc_types(
        profile=profile,
        query_spec=query_spec,
        active_hypothesis=active_hypothesis,
        hard_requirements=(
            _normalize_str_list(active_hypothesis.hard_requirements)
            if active_hypothesis and active_hypothesis.hard_requirements
            else hard_requirements
        ),
        is_pricing=profile == "pricing_profile",
        settings=get_settings(),
        retry_strategy=retry_strategy,
    )
    preferred_sources = _derive_preferred_sources(
        query_spec=query_spec,
        active_hypothesis=active_hypothesis,
        retry_strategy=retry_strategy,
    )

    return _build_plan_from_inputs(
        query=selected_query,
        attempt=attempt,
        profile=profile,
        query_keyword=keyword,
        query_semantic=semantic,
        fallback_queries=fallback_queries,
        hard_requirements=hard_requirements,
        preferred_doc_types=preferred_doc_types,
        excluded_doc_types=excluded_doc_types,
        preferred_sources=preferred_sources,
        active_hypothesis=active_hypothesis,
        retry_strategy=retry_strategy,
    )


async def build_retrieval_plan_for_attempt(
    *,
    base_query: str,
    attempt: int,
    query_spec: QuerySpec | None = None,
    retry_strategy: RetryStrategy | None = None,
    explicit_override: str | None = None,
    conversation_history: list[dict[str, str]] | None = None,
) -> tuple[RetrievalPlan, dict[str, Any]]:
    """Async planner entrypoint used by runtime retrieval pipeline."""
    selected_query, query_source, rewrite_candidates = resolve_retrieval_query(
        base_query=base_query,
        attempt=attempt,
        query_spec=query_spec,
        retry_strategy=retry_strategy,
        explicit_override=explicit_override,
    )

    hard_requirements = _resolve_hard_requirements(query_spec)
    fallback_profile: str | None = None

    if query_spec:
        keyword, semantic, fallback_queries = _resolve_queries_from_query_spec(
            selected_query=selected_query,
            query_source=query_source,
            query_spec=query_spec,
            retry_strategy=retry_strategy,
        )
    else:
        keyword = selected_query
        semantic = selected_query
        fallback_queries = rewrite_candidates[1:5]
        settings = get_settings()
        if settings.query_rewriter_use_llm:
            from app.services.query_rewriter import rewrite_for_retrieval

            retry_boost = ""
            if retry_strategy and retry_strategy.boost_patterns:
                retry_boost = " ".join(
                    p for p in retry_strategy.boost_patterns if not str(p).startswith("\\")
                )[:100]
            rewrite = await rewrite_for_retrieval(
                selected_query,
                conversation_history,
                retry_boost or None,
            )
            keyword = rewrite.keyword_query or selected_query
            semantic = rewrite.semantic_query or selected_query
            fallback_profile = rewrite.retrieval_profile

    active_hypothesis = _resolve_active_hypothesis(query_spec, retry_strategy=retry_strategy)
    profile = (
        sanitize_retrieval_profile(getattr(active_hypothesis, "retrieval_profile", ""))
        if active_hypothesis
        else None
    ) or _resolve_profile(query_spec, fallback_profile=fallback_profile)
    preferred_doc_types, excluded_doc_types = _derive_doc_types(
        profile=profile,
        query_spec=query_spec,
        active_hypothesis=active_hypothesis,
        hard_requirements=(
            _normalize_str_list(active_hypothesis.hard_requirements)
            if active_hypothesis and active_hypothesis.hard_requirements
            else hard_requirements
        ),
        is_pricing=profile == "pricing_profile",
        settings=get_settings(),
        retry_strategy=retry_strategy,
    )
    preferred_sources = _derive_preferred_sources(
        query_spec=query_spec,
        active_hypothesis=active_hypothesis,
        retry_strategy=retry_strategy,
    )

    plan = _build_plan_from_inputs(
        query=selected_query,
        attempt=attempt,
        profile=profile,
        query_keyword=keyword,
        query_semantic=semantic,
        fallback_queries=fallback_queries,
        hard_requirements=hard_requirements,
        preferred_doc_types=preferred_doc_types,
        excluded_doc_types=excluded_doc_types,
        preferred_sources=preferred_sources,
        active_hypothesis=active_hypothesis,
        retry_strategy=retry_strategy,
    )
    return plan, {
        "selected_retrieval_query": selected_query,
        "query_source": query_source,
        "rewrite_candidates": rewrite_candidates[:3],
    }
