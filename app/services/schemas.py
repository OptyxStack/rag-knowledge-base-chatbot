"""Service-layer schemas for Phase 2/3: QuerySpec, DecisionResult."""

from dataclasses import dataclass
from typing import Any


@dataclass
class QuerySpec:
    """Normalized query specification from Phase 2 Normalizer."""

    intent: str  # informational | transactional | policy | troubleshooting | account | ambiguous
    entities: list[str]  # domain objects extracted (vps, dedicated, pricing, etc.)
    constraints: dict[str, Any]  # budget, region, plan_type, etc.
    required_evidence: list[str]  # numbers, links, transaction_link, policy_clause, steps, citations
    risk_level: str  # low | medium | high
    keyword_queries: list[str]  # for BM25
    semantic_queries: list[str]  # for vector search
    clarifying_questions: list[str]  # for ASK_USER when ambiguous or missing constraints
    is_ambiguous: bool = False  # True when referent unclear (e.g. "what diff from this?")
    skip_retrieval: bool = False  # True when no retrieval needed (greeting, social)
    canned_response: str | None = None  # When skip_retrieval, use this (no LLM)
    canonical_query_en: str | None = None  # English translation when source was non-English (archi_v3)


@dataclass
class DecisionResult:
    """Decision Router output – Phase 3."""

    decision: str  # PASS | ASK_USER | ESCALATE
    reason: str  # sufficient | missing_constraints | missing_evidence_quality | ambiguous_query | high_risk_insufficient
    clarifying_questions: list[str]
    partial_links: list[str]  # for ASK_USER (evidence gap) – useful links to show
    answer: str = ""  # pre-generated response for ASK_USER/ESCALATE (no LLM call)
