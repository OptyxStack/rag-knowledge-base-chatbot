"""Service-layer schemas for the RAG pipeline.

These contracts are intentionally forward-compatible so newer orchestration
stages can be introduced without breaking the current runtime flow.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HypothesisSpec:
    """One retrieval/answering hypothesis for a query."""

    name: str
    evidence_families: list[str]
    answer_shape: str
    retrieval_profile: str = "generic_profile"
    required_evidence: list[str] | None = None
    hard_requirements: list[str] | None = None
    soft_requirements: list[str] | None = None
    doc_type_prior: list[str] | None = None
    preferred_sources: list[str] | None = None
    rewrite_candidates: list[str] | None = None
    query_hint: str | None = None


@dataclass
class HypothesisEvaluation:
    """Execution summary for one attempted hypothesis."""

    name: str
    retrieval_profile: str
    evidence_families: list[str]
    required_evidence: list[str]
    hard_requirements: list[str]
    evidence_count: int = 0
    quality_score: float = 0.0
    gate_pass: bool = False
    lane: str | None = None
    reason: str | None = None


@dataclass
class QuerySpec:
    """Normalized query specification from Phase 2 Normalizer."""

    intent: str  # informational | transactional | policy | troubleshooting | account | ambiguous
    entities: list[str]  # domain objects extracted (vps, dedicated, pricing, etc.)
    constraints: dict[str, Any]  # budget, region, plan_type, etc.
    required_evidence: list[str]  # policy_language | numbers_units | transaction_link | steps_structure | has_any_url
    risk_level: str  # low | medium | high
    keyword_queries: list[str]  # for BM25
    semantic_queries: list[str]  # for vector search
    clarifying_questions: list[str]  # backward-compatible general follow-up questions
    is_ambiguous: bool = False  # True when referent unclear (e.g. "what diff from this?")
    skip_retrieval: bool = False  # True when no retrieval needed (greeting, social)
    canned_response: str | None = None  # When skip_retrieval, use this (no LLM)
    canonical_query_en: str | None = None  # English translation when source was non-English (archi_v3)
    original_query: str | None = None  # Raw user input before translation / rewriting
    source_lang: str = "en"  # Detected source language
    translation_needed: bool = False  # True when source_lang != en and canonical query is used
    language_confidence: float | None = None  # Detector confidence when available
    user_goal: str = "unknown"  # price_lookup | order_link | refund_policy | setup_steps | general_info
    resolved_slots: dict[str, Any] | None = None  # Parsed explicit slots (os, region, billing_cycle, ...)
    missing_slots: list[str] | None = None  # Legacy alias for missing_info_for_refinement
    ambiguity_type: str | None = None  # referential | missing_constraints | semantic | None
    answerable_without_clarification: bool = True  # False only when clarification is truly required
    missing_info_blocking: list[str] | None = None  # Missing details that prevent a useful answer now
    missing_info_for_refinement: list[str] | None = None  # Missing details that only improve/refine the answer
    blocking_clarifying_questions: list[str] | None = None  # Questions to unblock the answer
    refinement_questions: list[str] | None = None  # Optional follow-up questions after a bounded answer
    assistant_should_lead: bool = False  # True when the assistant should suggest defaults/assumptions
    hard_requirements: list[str] | None = None  # Must-have evidence to answer safely
    soft_requirements: list[str] | None = None  # Nice-to-have evidence for a stronger answer
    evidence_families: list[str] | None = None  # capability_availability | pricing_limits | policy_terms | ...
    answer_shape: str = "direct_lookup"  # direct_lookup | yes_no | recommendation | comparison | procedural | bounded_summary
    primary_hypothesis: HypothesisSpec | None = None
    fallback_hypotheses: list[HypothesisSpec] | None = None
    doc_type_prior: list[str] | None = None  # Preferred doc types for retrieval (authoritative if provided)
    retrieval_profile: str = "generic_profile"  # pricing_profile | policy_profile | troubleshooting_profile | ...
    rewrite_candidates: list[str] | None = None  # Fallback rewritten queries for retrieval retry
    answer_mode_hint: str = "strong"  # strong | weak | ask_user
    extraction_mode: str = "rule_primary"  # llm_primary | rule_primary | rule_fallback
    config_overrides_applied: list[str] | None = None  # Enabled normalizer compatibility switches


@dataclass
class DecisionResult:
    """Decision Router output – Phase 3."""

    decision: str  # PASS | ASK_USER | ESCALATE
    reason: str  # sufficient | missing_constraints | missing_evidence_quality | ambiguous_query | high_risk_insufficient
    clarifying_questions: list[str]  # ASK_USER blockers or bounded-answer refinement follow-ups
    partial_links: list[str]  # for ASK_USER (evidence gap) – useful links to show
    answer: str = ""  # pre-generated response for ASK_USER/ESCALATE (no LLM call)
    answer_policy: str = "direct"  # direct | bounded | clarify | human_handoff
    lane: str | None = None  # PASS_STRONG | PASS_WEAK | ASK_USER | ESCALATE

    def resolved_lane(self) -> str:
        """Return explicit lane, defaulting to the legacy decision field."""
        return self.lane or self.decision


@dataclass
class RetrievalPlan:
    """Concrete retrieval strategy for one attempt."""

    profile: str
    attempt_index: int
    reason: str
    query_keyword: str
    query_semantic: str
    active_hypothesis_name: str = "primary"
    evidence_families: list[str] | None = None
    answer_shape: str = "direct_lookup"
    active_required_evidence: list[str] | None = None
    active_hard_requirements: list[str] | None = None
    active_soft_requirements: list[str] | None = None
    preferred_doc_types: list[str] | None = None  # Soft primary doc types used for the main retrieval pass
    excluded_doc_types: list[str] | None = None
    preferred_sources: list[str] | None = None  # Secondary co-equal sources (for example: conversation)
    authoritative_doc_types: list[str] | None = None
    supporting_doc_types: list[str] | None = None
    fallback_queries: list[str] | None = None
    bm25_weight: float = 1.0
    vector_weight: float = 1.0
    rerank_weight: float = 1.0
    fetch_n: int = 0
    rerank_k: int = 0
    enable_parent_expansion: bool = False
    enable_neighbor_expansion: bool = False
    enable_exact_slot_fetch: bool = False
    boost_patterns: list[str] | None = None
    exclude_patterns: list[str] | None = None
    budget_hint: dict[str, Any] | None = None


@dataclass
class CandidateChunk:
    """Intermediate retrieval candidate before final evidence selection."""

    chunk_id: str
    document_id: str
    source_url: str
    doc_type: str
    chunk_text: str
    retrieval_score: float
    retrieval_source: str  # bm25 | vector | boosted_fetch | expanded_parent | expanded_neighbor
    metadata: dict[str, Any] | None = None


@dataclass
class CandidatePool:
    """Broad candidate pool before evidence set construction."""

    items: list[CandidateChunk]
    source_counts: dict[str, int]
    doc_type_counts: dict[str, int]
    retrieval_stats: dict[str, Any]
    plan_used: RetrievalPlan | None = None


@dataclass
class EvidenceSet:
    """Answer-ready evidence bundle chosen from a candidate pool."""

    chunks: list[Any]
    primary_chunks: list[str]
    supporting_chunks: list[str]
    covered_requirements: list[str]
    uncovered_requirements: list[str]
    covered_slots: list[str]
    uncovered_slots: list[str]
    trust_mix: dict[str, float] | None = None
    diversity_score: float = 0.0
    concentration_score: float = 0.0
    evidence_summary: str = ""
    build_reason: str = ""


@dataclass
class EvidenceAssessment:
    """Structured judgment about whether evidence can support an answer."""

    coverage_score: float
    specificity_score: float
    actionability_score: float
    trust_score: float
    consistency_score: float
    can_answer_fully: bool
    can_answer_partially: bool
    missing_slots: list[str]
    weak_claim_areas: list[str]
    blocked_claim_areas: list[str]
    recommended_lane: str
    retry_value_estimate: float = 0.0
    reasoning: str = ""


@dataclass
class AnswerPlan:
    """Generation blueprint for one answer lane."""

    lane: str
    allowed_claim_scope: str  # full | partial | none
    must_include: list[str]
    must_avoid: list[str]
    required_citations: list[str]
    output_blocks: list[str]
    tone_policy: str = "concise"
    generation_constraints: dict[str, Any] | None = None


@dataclass
class AnswerDraft:
    """Structured answer before claim-level review."""

    lane: str
    direct_answer: str
    confirmed_points: list[str]
    uncertain_points: list[str]
    recommended_next_step: str
    citations: list[dict[str, Any]]
    confidence_band: str  # high | medium | low
    raw_text: str


@dataclass
class AnswerOutput:
    """Structured answer output from the RAG pipeline."""

    decision: str  # PASS | ASK_USER | ESCALATE
    answer: str
    followup_questions: list[str]
    citations: list[dict[str, str]]
    confidence: float
    debug: dict[str, Any] = field(default_factory=dict)


@dataclass
class ReviewResult:
    """Post-generation review outcome."""

    status: str  # accept | accept_with_lower_confidence | trim_unsupported_claims | retry_targeted | downgrade_lane | escalate
    unsupported_claims: list[str]
    weakly_supported_claims: list[str]
    claim_to_citation_map: dict[str, list[str]]
    reviewer_notes: list[str]
    final_lane: str
    suggested_retry_plan: RetrievalPlan | None = None
