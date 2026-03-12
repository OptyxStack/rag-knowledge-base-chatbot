"""Retrieval planning.

Single source of truth for:
- query rewrite selection per attempt
- retrieval profile/doc type/hard requirement policy
- RetrievalPlan construction

When QuerySpec is present, retrieval_profile/doc_type_prior are soft hints.
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
_DEFAULT_DIVERSITY_DOC_TYPES = ("howto", "docs", "faq", "conversation")
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
_PRODUCT_FAMILIES = {"windows_vps", "kvm_vps", "macos_vps", "dedicated"}
_ANSWER_TYPE_PAGE_KIND_HINTS: dict[str, dict[str, Any]] = {
    "direct_link": {
        "preferred_page_kinds": ["order_page", "product_page"],
        "supporting_page_kinds": ["pricing_table", "howto"],
        "page_kind_weights": {"order_page": 1.45, "product_page": 1.25, "pricing_table": 1.1},
        "demote_doc_types": ["faq", "blog"],
    },
    "pricing": {
        "preferred_page_kinds": ["pricing_table", "product_page"],
        "supporting_page_kinds": ["order_page", "howto"],
        "page_kind_weights": {"pricing_table": 1.4, "product_page": 1.15, "order_page": 1.1},
        "demote_doc_types": ["faq", "blog"],
    },
    "policy": {
        "preferred_page_kinds": ["policy"],
        "supporting_page_kinds": ["howto", "faq"],
        "page_kind_weights": {"policy": 1.5, "howto": 1.05},
        "demote_doc_types": ["blog"],
    },
    "troubleshooting": {
        "preferred_page_kinds": ["howto", "faq"],
        "supporting_page_kinds": ["product_page", "pricing_table"],
        "page_kind_weights": {"howto": 1.35, "faq": 1.2, "product_page": 1.05},
        "demote_doc_types": ["blog"],
    },
    "general": {
        "preferred_page_kinds": ["product_page", "howto"],
        "supporting_page_kinds": ["faq", "pricing_table"],
        "page_kind_weights": {"product_page": 1.1, "howto": 1.08},
        "demote_doc_types": ["blog"],
    },
}
_ANSWER_TYPE_DOC_TYPE_HINTS: dict[str, list[str]] = {
    "direct_link": ["pricing", "docs", "howto"],
    "pricing": ["pricing", "tos", "docs"],
    "policy": ["policy", "tos", "docs"],
    "troubleshooting": ["howto", "docs", "faq"],
    "general": ["docs", "faq"],
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


def _sanitize_answer_type(value: Any) -> str:
    raw = str(value or "").strip().lower()
    aliases = {
        "link": "direct_link",
        "order_link": "direct_link",
        "price_lookup": "pricing",
        "refund_policy": "policy",
    }
    normalized = aliases.get(raw, raw)
    return normalized or "general"


def _normalize_product_family(value: Any) -> str | None:
    raw = str(value or "").strip().lower()
    if not raw:
        return None
    aliases = {
        "windows": "windows_vps",
        "windows vps": "windows_vps",
        "windows_vps": "windows_vps",
        "windows-rdp": "windows_vps",
        "rdp": "windows_vps",
        "kvm": "kvm_vps",
        "kvm vps": "kvm_vps",
        "kvm_vps": "kvm_vps",
        "linux_vps": "kvm_vps",
        "linux vps": "kvm_vps",
        "linux": "kvm_vps",
        "macos": "macos_vps",
        "mac": "macos_vps",
        "macos vps": "macos_vps",
        "macos_vps": "macos_vps",
        "dedicated": "dedicated",
        "dedicated_server": "dedicated",
        "dedicated server": "dedicated",
        "dedicated_servers": "dedicated",
    }
    normalized = aliases.get(raw, raw)
    return normalized if normalized in _PRODUCT_FAMILIES else None


def _derive_product_family_hints(query_spec: QuerySpec | None) -> list[str]:
    if not query_spec:
        return []
    hints: list[str] = []
    resolved_slots = getattr(query_spec, "resolved_slots", None) or {}
    product_type = str(resolved_slots.get("product_type", "") or "").strip().lower()
    os_name = str(resolved_slots.get("os", "") or "").strip().lower()
    if product_type == "vps" and os_name == "windows":
        hints.append("windows_vps")
    elif product_type == "vps" and os_name in {"linux", "kvm"}:
        hints.append("kvm_vps")
    elif product_type == "vps" and os_name in {"mac", "macos"}:
        hints.append("macos_vps")
    elif product_type in {"dedicated", "dedicated_server", "dedicated_servers"}:
        hints.append("dedicated")
    for candidate in (
        getattr(query_spec, "target_entity", None),
        resolved_slots.get("product_type") if resolved_slots else None,
        *list(getattr(query_spec, "entities", None) or []),
    ):
        normalized = _normalize_product_family(candidate)
        if normalized and normalized not in hints:
            hints.append(normalized)
    return hints


def _derive_answer_type_hints(answer_type: str) -> dict[str, Any]:
    return dict(_ANSWER_TYPE_PAGE_KIND_HINTS.get(_sanitize_answer_type(answer_type), _ANSWER_TYPE_PAGE_KIND_HINTS["general"]))


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
    """Infer retrieval profile from evidence_families and required_evidence (LLM output).
    Principle-based: use what the user needs, not keyword mapping."""
    req = {x.lower() for x in _normalize_str_list(required_evidence)}
    hard = {h.lower() for h in _normalize_str_list(hard_requirements)}
    combined = req | hard
    families = _normalize_str_list(evidence_families)
    for family in families:
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
    """Resolve retrieval query for this attempt from QuerySpec/retry inputs.

    Retry strategy (suggested_query, boost_patterns, etc.) comes from Evidence Evaluator LLM.
    Fallback: query_spec.rewrite_candidates from Normalizer LLM. No hardcoded product rules."""
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
    """Resolve doc-type hints from QuerySpec (soft preference only)."""
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
    add_tos_for_pricing: bool = False,
) -> tuple[list[str], list[str]]:
    all_doc_types = _sanitize_doc_type_list(
        (active_hypothesis.doc_type_prior if active_hypothesis and active_hypothesis.doc_type_prior else preferred_doc_types)
    )
    if add_tos_for_pricing and "tos" not in {d.lower() for d in all_doc_types}:
        all_doc_types = list(all_doc_types) + ["tos"]
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
    required_evidence: list[str] | None = None,
    evidence_families: list[str] | None = None,
    answer_type: str,
    is_pricing: bool,
    settings,
    retry_strategy: RetryStrategy | None = None,
) -> tuple[list[str], list[str]]:
    preferred: list[str] = []
    excluded: list[str] = []
    hard = {h.lower() for h in hard_requirements}
    req_ev = {e.lower() for e in _normalize_str_list(required_evidence)}
    families = set(_normalize_str_list(evidence_families))

    hinted: list[str] = []
    if active_hypothesis and getattr(active_hypothesis, "doc_type_prior", None):
        hinted.extend(_sanitize_doc_type_list(active_hypothesis.doc_type_prior))
    elif query_spec is not None:
        hinted.extend(_resolve_doc_type_prior(query_spec))

    answer_type_defaults = _sanitize_doc_type_list(
        _ANSWER_TYPE_DOC_TYPE_HINTS.get(_sanitize_answer_type(answer_type), _ANSWER_TYPE_DOC_TYPE_HINTS["general"])
    )

    profile_defaults: list[str] = []
    if is_pricing:
        configured = [
            t.strip()
            for t in str(getattr(settings, "retrieval_plans_fetch_doc_types", "") or "").split(",")
            if t.strip()
        ]
        profile_defaults.extend(configured or ["pricing"])
    if profile == "policy_profile" or "policy_language" in hard:
        configured = [
            t.strip()
            for t in str(getattr(settings, "retrieval_policy_doc_types", "") or "").split(",")
            if t.strip()
        ]
        profile_defaults.extend(configured or ["policy", "tos"])
    if profile == "troubleshooting_profile" or "steps_structure" in hard:
        profile_defaults.extend(["howto", "docs", "faq"])

    preferred.extend(hinted)
    preferred.extend(answer_type_defaults)
    preferred.extend(_sanitize_doc_type_list(profile_defaults))

    if profile == "generic_profile":
        evidence_broaden: list[str] = []
        if req_ev & {"numbers_units", "transaction_link", "has_any_url"} or families & {"pricing_limits", "transactional_link"}:
            evidence_broaden.extend(["pricing", "tos"])
        if req_ev & {"steps_structure"} or families & {"troubleshooting_steps"}:
            evidence_broaden.extend(["howto", "docs"])
        if evidence_broaden:
            preferred.extend(evidence_broaden)
        preferred_set = {p.lower() for p in preferred}
        if len(preferred_set) <= 2:
            for d in ("pricing", "howto", "faq", "docs"):
                if d not in preferred_set:
                    preferred.append(d)
                    preferred_set.add(d)

    if retry_strategy and retry_strategy.filter_doc_types:
        retry_doc_types = _normalize_str_list(retry_strategy.filter_doc_types)
        preferred = list(dict.fromkeys(retry_doc_types + preferred))

    # ToS often contains add-on pricing (IP, bandwidth, storage). Include for pricing_profile.
    if (profile == "pricing_profile" or is_pricing) and "tos" not in {p.lower() for p in preferred}:
        preferred.append("tos")

    return list(dict.fromkeys(preferred)), excluded


def _derive_diversity_doc_types(
    *,
    settings,
    preferred_doc_types: list[str],
    retry_strategy: RetryStrategy | None = None,
) -> list[str]:
    """Resolve doc types for diversity fan-out retrieval, independent from normalizer output."""
    if not bool(getattr(settings, "retrieval_diversity_enabled", False)):
        return []
    configured_raw = str(getattr(settings, "retrieval_diversity_doc_types", "") or "").strip()
    configured = (
        _sanitize_doc_type_list([t.strip() for t in configured_raw.split(",") if t.strip()])
        if configured_raw
        else _sanitize_doc_type_list(list(_DEFAULT_DIVERSITY_DOC_TYPES))
    )
    if not configured:
        configured = _sanitize_doc_type_list(list(_DEFAULT_DIVERSITY_DOC_TYPES))

    max_doc_types = int(getattr(settings, "retrieval_diversity_max_doc_types", 4) or 4)
    max_doc_types = max(1, max_doc_types)

    retry_filter = set()
    if retry_strategy and retry_strategy.filter_doc_types:
        retry_filter = {d.lower() for d in _normalize_str_list(retry_strategy.filter_doc_types)}
    if retry_filter:
        filtered = [d for d in configured if d.lower() in retry_filter]
        if filtered:
            configured = filtered

    preferred_set = {d.lower() for d in preferred_doc_types}
    prioritized = [d for d in configured if d.lower() in preferred_set]
    for d in configured:
        if d not in prioritized:
            prioritized.append(d)
    return prioritized[:max_doc_types]


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
    answer_type: str = "general",
    product_family_hints: list[str] | None = None,
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
        add_tos_for_pricing=profile == "pricing_profile",
    )
    answer_type_hints = _derive_answer_type_hints(answer_type)
    preferred_page_kinds = _normalize_str_list(answer_type_hints.get("preferred_page_kinds"))
    supporting_page_kinds = _normalize_str_list(answer_type_hints.get("supporting_page_kinds"))
    page_kind_weights = dict(answer_type_hints.get("page_kind_weights") or {})
    demote_doc_types = _sanitize_doc_type_list(answer_type_hints.get("demote_doc_types") or [])
    diversity_doc_types = _derive_diversity_doc_types(
        settings=settings,
        preferred_doc_types=preferred_doc_types,
        retry_strategy=retry_strategy,
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
            "diversity_doc_types": diversity_doc_types,
            "diversity_fetch_per_type": int(getattr(settings, "retrieval_diversity_fetch_per_type", 6) or 6),
            "answer_type": _sanitize_answer_type(answer_type),
            "preferred_page_kinds": preferred_page_kinds,
            "supporting_page_kinds": supporting_page_kinds,
            "page_kind_weights": page_kind_weights,
            "product_family_hints": _normalize_str_list(product_family_hints or []),
            "product_family_weights": {pf: 1.2 for pf in _normalize_str_list(product_family_hints or [])},
            "demote_doc_types": demote_doc_types,
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

    answer_type = _sanitize_answer_type(
        getattr(query_spec, "answer_type", None)
        if query_spec
        else ("pricing" if profile == "pricing_profile" else "general")
    )
    active_req = _normalize_str_list(
        getattr(active_hypothesis, "required_evidence", None) if active_hypothesis
        else (getattr(query_spec, "required_evidence", None) if query_spec else [])
    ) or _normalize_str_list(getattr(query_spec, "required_evidence", None) if query_spec else [])
    active_families = _normalize_str_list(
        getattr(active_hypothesis, "evidence_families", None) if active_hypothesis
        else (getattr(query_spec, "evidence_families", None) if query_spec else [])
    )
    preferred_doc_types, excluded_doc_types = _derive_doc_types(
        profile=profile,
        query_spec=query_spec,
        active_hypothesis=active_hypothesis,
        hard_requirements=(
            _normalize_str_list(active_hypothesis.hard_requirements)
            if active_hypothesis and active_hypothesis.hard_requirements
            else hard_requirements
        ),
        required_evidence=active_req,
        evidence_families=active_families,
        answer_type=answer_type,
        is_pricing=profile == "pricing_profile",
        settings=get_settings(),
        retry_strategy=retry_strategy,
    )
    preferred_sources = _derive_preferred_sources(
        query_spec=query_spec,
        active_hypothesis=active_hypothesis,
        retry_strategy=retry_strategy,
    )
    product_family_hints = _derive_product_family_hints(query_spec)

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
        answer_type=answer_type,
        product_family_hints=product_family_hints,
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
    answer_type = _sanitize_answer_type(
        getattr(query_spec, "answer_type", None)
        if query_spec
        else ("pricing" if profile == "pricing_profile" else "general")
    )
    active_req = _normalize_str_list(
        getattr(active_hypothesis, "required_evidence", None) if active_hypothesis
        else (getattr(query_spec, "required_evidence", None) if query_spec else [])
    ) or _normalize_str_list(getattr(query_spec, "required_evidence", None) if query_spec else [])
    active_families = _normalize_str_list(
        getattr(active_hypothesis, "evidence_families", None) if active_hypothesis
        else (getattr(query_spec, "evidence_families", None) if query_spec else [])
    )
    preferred_doc_types, excluded_doc_types = _derive_doc_types(
        profile=profile,
        query_spec=query_spec,
        active_hypothesis=active_hypothesis,
        hard_requirements=(
            _normalize_str_list(active_hypothesis.hard_requirements)
            if active_hypothesis and active_hypothesis.hard_requirements
            else hard_requirements
        ),
        required_evidence=active_req,
        evidence_families=active_families,
        answer_type=answer_type,
        is_pricing=profile == "pricing_profile",
        settings=get_settings(),
        retry_strategy=retry_strategy,
    )
    preferred_sources = _derive_preferred_sources(
        query_spec=query_spec,
        active_hypothesis=active_hypothesis,
        retry_strategy=retry_strategy,
    )
    product_family_hints = _derive_product_family_hints(query_spec)

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
        answer_type=answer_type,
        product_family_hints=product_family_hints,
        retry_strategy=retry_strategy,
    )
    return plan, {
        "selected_retrieval_query": selected_query,
        "query_source": query_source,
        "rewrite_candidates": rewrite_candidates[:3],
    }
