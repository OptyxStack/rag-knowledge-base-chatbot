"""Retry Planner – LLM-driven. No hardcoded rules.

Attempt 1: Broad hybrid (unchanged).
Attempt 2: Retry strategy from Evidence Evaluator (LLM) or query_spec rewrite_candidates.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.logging import get_logger
from app.services.doc_type_service import get_valid_doc_type_keys

if TYPE_CHECKING:
    from app.services.evidence_evaluator import EvidenceEvalResult
    from app.services.schemas import QuerySpec

logger = get_logger(__name__)

_ANSWER_TYPE_ALIASES = {
    "link": "direct_link",
    "order_link": "direct_link",
    "buy_link": "direct_link",
    "price": "pricing",
}


def _normalize_answer_type(value: str | None) -> str:
    raw = str(value or "").strip().lower()
    return _ANSWER_TYPE_ALIASES.get(raw, raw or "general")


def _normalize_entity_phrase(value: str | None) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    aliases = {
        "windows": "windows vps",
        "windows_vps": "windows vps",
        "windows-rdp": "windows vps",
        "kvm": "kvm vps",
        "kvm_vps": "kvm vps",
        "linux_vps": "kvm vps",
        "mac": "macos vps",
        "macos": "macos vps",
        "macos_vps": "macos vps",
        "dedicated_server": "dedicated server",
        "dedicated_servers": "dedicated server",
        "refund_policy": "refund policy",
    }
    text = aliases.get(text, text)
    return text.replace("_", " ").replace("-", " ").strip()


def plan_targeted_retry_queries(
    *,
    expected_answer_type: str | None,
    target_entity: str | None,
    query: str,
    max_queries: int = 3,
) -> list[str]:
    """Build focused retry queries from verifier failure reason/context.

    Used by Phase 5 repair loop after verify when candidate answer type mismatches.
    """
    answer_type = _normalize_answer_type(expected_answer_type)
    entity = _normalize_entity_phrase(target_entity)
    base = str(query or "").strip()

    candidates: list[str] = []
    if answer_type == "direct_link":
        scope = entity or "official service"
        candidates.extend(
            [
                f"{scope} order page",
                f"{scope} product page",
                f"{scope} official order link",
            ]
        )
    elif answer_type == "pricing":
        scope = entity or "service"
        candidates.extend(
            [
                f"{scope} pricing table",
                f"{scope} monthly pricing",
                f"{scope} order pricing",
            ]
        )
    elif answer_type == "policy":
        scope = entity or "refund policy"
        if "policy" not in scope:
            scope = f"{scope} policy"
        candidates.extend(
            [
                f"{scope} terms of service",
                f"{scope} official policy",
                f"{scope} clause",
            ]
        )

    if base:
        candidates.append(base)

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        q = " ".join(str(candidate).split()).strip()
        if not q:
            continue
        key = q.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(q)
        if len(deduped) >= max(1, max_queries):
            break
    return deduped


@dataclass
class RetryStrategy:
    """Strategy for Attempt 2 retrieval. From LLM or query_spec."""

    boost_patterns: list[str] = field(default_factory=list)
    filter_doc_types: list[str] | None = None
    exclude_patterns: list[str] = field(default_factory=list)
    context_expansion: bool = False
    suggested_query: str | None = None
    hypothesis_index: int | None = None
    hypothesis_name: str | None = None
    required_evidence_override: list[str] | None = None
    hard_requirements_override: list[str] | None = None
    soft_requirements_override: list[str] | None = None
    preferred_sources_override: list[str] | None = None
    retrieval_profile_override: str | None = None
    answer_shape_override: str | None = None


def plan_retry(
    missing_signals: list[str],
    attempt: int,
    evidence_eval_result: "EvidenceEvalResult | None" = None,
    query_spec: "QuerySpec | None" = None,
) -> RetryStrategy | None:
    """Plan retry strategy for Attempt 2. LLM-driven; no hardcoded rules.

    Uses evidence_eval_result (Evidence Evaluator LLM) when retry_needed.
    Fallback: suggested_query from query_spec.rewrite_candidates.
    """
    if attempt < 2:
        return None

    if not missing_signals:
        return None

    suggested_query: str | None = None
    boost_patterns: list[str] = []
    filter_doc_types: list[str] | None = None
    hypothesis_index: int | None = None
    hypothesis_name: str | None = None
    required_evidence_override: list[str] | None = None
    hard_requirements_override: list[str] | None = None
    soft_requirements_override: list[str] | None = None
    preferred_sources_override: list[str] | None = None
    retrieval_profile_override: str | None = None
    answer_shape_override: str | None = None

    if evidence_eval_result and evidence_eval_result.retry_needed:
        suggested_query = evidence_eval_result.suggested_query
        boost_patterns = list(getattr(evidence_eval_result, "retry_boost_terms", None) or [])
        raw_doc_types = list(getattr(evidence_eval_result, "retry_doc_types", None) or [])
        if raw_doc_types:
            valid = set(get_valid_doc_type_keys())
            filter_doc_types = [t for t in raw_doc_types if t in valid] if valid else raw_doc_types
    elif query_spec and getattr(query_spec, "rewrite_candidates", None):
        candidates = query_spec.rewrite_candidates or []
        if len(candidates) > 1:
            candidate_idx = min(attempt - 1, len(candidates) - 1)
            suggested_query = candidates[candidate_idx]

    if query_spec and getattr(query_spec, "fallback_hypotheses", None):
        fallbacks = query_spec.fallback_hypotheses or []
        fb_idx = min(max(attempt - 2, 0), len(fallbacks) - 1)
        if 0 <= fb_idx < len(fallbacks):
            hypothesis = fallbacks[fb_idx]
            hypothesis_index = fb_idx + 1  # 0 = primary
            hypothesis_name = getattr(hypothesis, "name", None) or f"fallback_{fb_idx + 1}"
            required_evidence_override = list(getattr(hypothesis, "required_evidence", None) or [])
            hard_requirements_override = list(getattr(hypothesis, "hard_requirements", None) or [])
            soft_requirements_override = list(getattr(hypothesis, "soft_requirements", None) or [])
            preferred_sources_override = list(getattr(hypothesis, "preferred_sources", None) or [])
            retrieval_profile_override = getattr(hypothesis, "retrieval_profile", None)
            answer_shape_override = getattr(hypothesis, "answer_shape", None)
            if not suggested_query and getattr(hypothesis, "query_hint", None):
                suggested_query = str(hypothesis.query_hint).strip()

    if (
        not suggested_query
        and not boost_patterns
        and not filter_doc_types
        and hypothesis_index is None
    ):
        return None

    logger.debug(
        "retry_planner",
        missing_signals=missing_signals[:3],
        suggested_query_preview=suggested_query[:50] if suggested_query else None,
    )
    return RetryStrategy(
        boost_patterns=boost_patterns,
        filter_doc_types=filter_doc_types,
        exclude_patterns=[],
        context_expansion=False,
        suggested_query=suggested_query,
        hypothesis_index=hypothesis_index,
        hypothesis_name=hypothesis_name,
        required_evidence_override=required_evidence_override,
        hard_requirements_override=hard_requirements_override,
        soft_requirements_override=soft_requirements_override,
        preferred_sources_override=preferred_sources_override,
        retrieval_profile_override=retrieval_profile_override,
        answer_shape_override=answer_shape_override,
    )
