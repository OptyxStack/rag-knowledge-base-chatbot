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


@dataclass
class RetryStrategy:
    """Strategy for Attempt 2 retrieval. From LLM or query_spec."""

    boost_patterns: list[str] = field(default_factory=list)
    filter_doc_types: list[str] | None = None
    exclude_patterns: list[str] = field(default_factory=list)
    context_expansion: bool = False
    suggested_query: str | None = None


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
    if attempt != 2:
        return None

    if not missing_signals:
        return None

    suggested_query: str | None = None
    boost_patterns: list[str] = []
    filter_doc_types: list[str] | None = None

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
            suggested_query = candidates[1]

    if not suggested_query and not boost_patterns and not filter_doc_types:
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
    )
