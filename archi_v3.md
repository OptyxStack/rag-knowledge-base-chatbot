archi_v3.md
Enterprise RAG v3 — Hybrid Language-Aware LLM Orchestrated Architecture
1. Design Principles
1.1 Core Philosophy

This system follows:

LLM-first semantic reasoning + Deterministic guardrails for safety and correctness

We do NOT separate translation as a standalone stage.
Instead, the LLM Normalizer performs language-aware canonicalization and semantic extraction in a single step.

1.2 Architectural Separation
Layer	Responsibility
LLM	Understand, structure, reason, critique
Deterministic	Enforce rules, budgets, safety, citation validity
Retrieval	Fetch evidence
Reviewer	Multi-layer quality assurance
1.3 Quality Goals

Maximize semantic understanding

Maximize grounding

Prevent hallucination

Preserve auditability

Maintain structured intermediate outputs

Avoid infinite retry loops

2. High-Level System Flow
Input
  ↓
Language Detect (fast, non-LLM)
  ↓
LLM Normalizer (language-aware)
  - internal translation if needed
  - canonical English representation
  - deep QuerySpec extraction
  - detect ambiguity
  - infer required_evidence
  ↓
Retrieval Attempt 1 (Hybrid BM25 + Vector + RRF + Rerank)
  ↓
LLM Evidence Evaluator
  ↓
Deterministic Evidence Quality Gate
  ├── FAIL → Retry Planner → Retrieval Attempt 2
  └── PASS
        ↓
Hybrid Decision Router (Deterministic + LLM gray zone)
  ├── ASK_USER
  ├── ESCALATE
  └── PASS
        ↓
LLM Answer Generation
        ↓
LLM Self-Critic
        ↓
Deterministic Citation Validator
        ↓
LLM Final Polish
        ↓
Output (English)
3. Execution Pipeline (Controller-Level Logic)
def handle_request(user_input):

    source_lang = detect_language(user_input)

    query_spec = llm_normalize(
        original_text=user_input,
        source_lang=source_lang
    )

    if query_spec.intent_cache_match:
        return intent_cache_answer

    if query_spec.is_ambiguous:
        return ask_user(query_spec.clarifying_questions)

    evidence = retrieval_attempt_1(query_spec)

    eval_result = llm_evidence_evaluator(query_spec, evidence)

    if not deterministic_quality_gate(evidence, query_spec):
        retry_strategy = build_retry_strategy(eval_result)
        evidence = retrieval_attempt_2(query_spec, retry_strategy)

    decision = decision_router(query_spec, evidence)

    if decision in ["ASK_USER", "ESCALATE"]:
        return decision_response

    answer = llm_generate(query_spec, evidence)

    critique = llm_self_critic(query_spec, answer, evidence)

    if critique.fail and regenerate_budget_available:
        answer = llm_regenerate_with_feedback(answer, critique)

    validate_citations(answer, evidence)

    answer = llm_final_polish(answer)

    return answer  # Output always English
4. Core Components
4.1 Language Detection

Fast, non-LLM (fastText / langdetect)

Returns source_lang

No standalone translation stage

4.2 LLM Normalizer (Hybrid Core)
Responsibilities

Translate internally (if non-English)

Produce canonical English representation

Extract deep semantic structure

Detect ambiguity

Infer required evidence

Assign risk level

Generate retrieval rewrites

Output Schema
{
  "canonical_query_en": "...",
  "intent": "...",
  "entities": [...],
  "constraints": {...},
  "required_evidence": [...],
  "risk_level": "...",
  "is_ambiguous": false,
  "clarifying_questions": [],
  "query_rewrites": {
    "keyword_queries": [...],
    "semantic_queries": [...]
  }
}
4.3 Retrieval Engine

Hybrid retrieval:

BM25 (OpenSearch)

Vector search (Qdrant)

RRF merge

Rerank (cross-encoder or LLM-based)

Attempt 1:

Broad search

Attempt 2:

Targeted by Retry Planner

Boost patterns

Exclude boilerplate

Context expansion (parent + neighbors)

Maximum attempts: 2

4.4 LLM Evidence Evaluator
Purpose

Semantic review of retrieved evidence.

Evaluates:

Relevance

Coverage gaps

Missing details

Hallucination risk

Output example:

{
  "relevance_score": 0.82,
  "coverage_gaps": ["missing SLA details"],
  "retry_needed": true,
  "suggested_query": "..."
}

This stage advises but does not override deterministic gates.

4.5 Deterministic Evidence Quality Gate

Hard checks:

Required feature thresholds:

numbers_units

transaction_link

policy_language

steps_structure

Relevance minimum score

High-risk policy requirements

PASS condition:

All required features meet thresholds

Minimum relevance satisfied

This stage is non-LLM and audit-safe.

4.6 Hybrid Decision Router

Deterministic first:
- High-risk + insufficient policy evidence → ESCALATE (LLM cannot override)
- Other hard rules

LLM for gray zone (when deterministic unclear):
- Quality gate pass but evidence semantically weak
- Quality gate fail but partial useful info
- Ambiguous risk level

Output: PASS | ASK_USER | ESCALATE. LLM cannot downgrade ESCALATE → PASS.

4.7 LLM Answer Generation

Structured JSON output

Must include citations

Grounded only on EvidencePack

4.8 LLM Self-Critic

Internal QA pass:

Checks:

Unsupported claims

Incomplete answer

Missing critical comparisons

Overgeneralization

If fail:

Single regenerate allowed

4.9 Deterministic Citation Validator

Ensures:

Citation IDs exist

Citation coverage >= threshold

No fabricated references

Non-LLM.

4.10 LLM Final Polish

Enhances:

Clarity

Structure

Tone

Helpfulness

Cannot modify factual content.

5. Retry & Budget Policy

Retrieval attempts: max 2

Generation attempts: max 2

Self-critic regenerate: max 1

No infinite loops

All retries logged with reason

6. High-Risk Handling

If:

Billing dispute

Legal claim

Abuse report

Refund denial

SLA conflict

AND insufficient policy evidence:

→ Deterministic ESCALATE

LLM cannot bypass.

7. Observability

Log per request:

source_lang

canonical_query_en

QuerySpec

retrieval_stats

evidence_quality_scores

retry_reason

decision

citation_coverage

critique_result

final_confidence

8. Architectural Advantages

Full semantic normalization

Multi-layer grounding

Hallucination resistance

Language-agnostic input

Deterministic safety

LLM-powered reasoning depth

Enterprise auditability

9. Future Extensions

Dual-LLM adversarial QA mode

Multi-agent retrieval planners

Adaptive evidence thresholds

Knowledge graph integration

Tool-calling agents

10. Summary

Enterprise RAG v3 achieves:

LLM-driven semantic understanding

Deterministic correctness enforcement

Multi-stage review pipeline

Language-aware canonical reasoning

Production-grade structur