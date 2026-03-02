"""Retry Planner – Phase 1: Fixed ladder, max 2 attempts.

Attempt 1: Broad hybrid (unchanged).
Attempt 2: Precision targeted by missing_signals + optional context expansion.
LLM Evidence Evaluator suggested_query used when retry_needed (archi_v3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.evidence_evaluator import EvidenceEvalResult

logger = get_logger(__name__)


@dataclass
class RetryStrategy:
    """Strategy for Attempt 2 retrieval."""

    boost_patterns: list[str] = field(default_factory=list)
    filter_doc_types: list[str] | None = None
    exclude_patterns: list[str] = field(default_factory=list)
    context_expansion: bool = False
    suggested_query: str | None = None  # From LLM Evidence Evaluator when retry_needed


# Mapping: missing_signal → RetryStrategy components
MISSING_SIGNAL_STRATEGIES: dict[str, dict] = {
    "missing_numbers": {
        "boost_patterns": ["$", "USD", "/mo", "monthly", "\\d+", "%", "VND", "pricing"],
        "filter_doc_types": None,
        "exclude_patterns": [],
        "context_expansion": False,
    },
    "missing_links": {
        "boost_patterns": ["https://", "http://", "www.", "order", "store"],
        "filter_doc_types": None,
        "exclude_patterns": [],
        "context_expansion": True,  # parent + neighbors for URL-rich chunks
    },
    "missing_transaction_link": {
        "boost_patterns": ["order", "checkout", "store", "cart", "subscribe", "buy"],
        "filter_doc_types": None,
        "exclude_patterns": [],
        "context_expansion": True,
    },
    "missing_policy": {
        "boost_patterns": ["policy", "terms", "refund", "eligible", "must", "shall"],
        "filter_doc_types": ["policy", "tos"],
        "exclude_patterns": [],
        "context_expansion": False,
    },
    "missing_steps": {
        "boost_patterns": ["step", "1.", "2.", "first", "second", "then"],
        "filter_doc_types": None,
        "exclude_patterns": [],
        "context_expansion": False,
    },
    "boilerplate_risk": {
        "boost_patterns": [],
        "filter_doc_types": None,
        "exclude_patterns": ["menu", "footer", "copyright", "contact us"],
        "context_expansion": True,  # default when boilerplate high: parent + neighbors
    },
    "staleness_risk": {
        "boost_patterns": [],  # would boost effective_date if index has it
        "filter_doc_types": None,
        "exclude_patterns": [],
        "context_expansion": False,
    },
}


def plan_retry(
    missing_signals: list[str],
    attempt: int,
    evidence_eval_result: "EvidenceEvalResult | None" = None,
) -> RetryStrategy | None:
    """Plan retry strategy for Attempt 2 based on missing_signals.

    Attempt 1: returns None (broad hybrid, no change).
    Attempt 2: returns RetryStrategy with precision targeting.
    """
    if attempt != 2:
        return None

    if not missing_signals:
        return None

    boost_patterns: list[str] = []
    filter_doc_types: list[str] | None = None
    exclude_patterns: list[str] = []
    context_expansion = False

    for sig in missing_signals:
        strat = MISSING_SIGNAL_STRATEGIES.get(sig)
        if strat:
            boost_patterns.extend(strat.get("boost_patterns", []))
            if strat.get("filter_doc_types"):
                filter_doc_types = strat["filter_doc_types"]
            exclude_patterns.extend(strat.get("exclude_patterns", []))
            if strat.get("context_expansion"):
                context_expansion = True

    # Dedupe
    boost_patterns = list(dict.fromkeys(boost_patterns))
    exclude_patterns = list(dict.fromkeys(exclude_patterns))

    suggested_query: str | None = None
    if evidence_eval_result and evidence_eval_result.retry_needed and evidence_eval_result.suggested_query:
        suggested_query = evidence_eval_result.suggested_query

    strategy = RetryStrategy(
        boost_patterns=boost_patterns,
        filter_doc_types=filter_doc_types,
        exclude_patterns=exclude_patterns,
        context_expansion=context_expansion,
        suggested_query=suggested_query,
    )

    logger.debug(
        "retry_planner",
        missing_signals=missing_signals,
        boost_patterns=boost_patterns[:5],
        context_expansion=context_expansion,
    )
    return strategy
