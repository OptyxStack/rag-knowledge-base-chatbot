Findings

The biggest reliability bug is not in retrieval at all: Redis LLM caching can collapse many prompts onto one app-level key. The gateway computes a per-prompt key, then overwrites it with prompt_cache_key before writing the cache entry, so unrelated normalizer/router/generation calls can reuse the wrong response. That can manifest as random routing drift or hallucinated stale answers. See llm_gateway.py, llm_gateway.py, llm_gateway.py.
Your control flow ignores the generator’s decision. generate parses a model decision, but verify hardcodes decision="PASS", and PASS_WEAK outputs are coerced back to PASS. That means the last model that actually saw the evidence cannot veto generation. See generate.py, answer_utils.py, verify.py.
The normalizer is too weak to be the source of truth. It explicitly treats required_evidence as soft, leaves hard_requirements empty, and always emits retrieval_profile="generic_profile". So later stages compensate with duplicated heuristics. That is the core architectural problem. See normalizer.py, normalizer.py, normalizer.py.
Retrieval logic is split across too many places: planner heuristics, answer_utils.resolve_retrieval_query, retrieval-service heuristics, retry planner, evidence selector, then chunk filter. That is not “robustness”; it is policy fragmentation. See retrieval_planner.py, answer_utils.py, retrieval.py.
Evidence quality and routing are unsafe in the exact places that should be strict. The quality gate can flip a fail to pass from phrasing in reason, and if gate_pass=True but coverage is missing, hard requirements are effectively bypassed. Then the decision-router LLM can override ASK_USER to PASS without seeing evidence text, only summary metadata. See evidence_quality.py, evidence_quality.py, decision_router.py.
Review

The true bottleneck is query understanding feeding retrieval. The system pretends QuerySpec is the control plane, but because the normalizer emits generic profiles and soft requirements, retrieval falls back to scattered keyword heuristics and doc-type forcing. The second bottleneck is evidence representation: BM25 returns highlight snippets while vector returns full chunks, yet both are merged and reranked together, which distorts ranking and later evidence selection. See opensearch_client.py, opensearch_client.py, retrieval.py.

Main problem source, in order: retrieval coverage first, query rewrite/planning second, hallucination control third, evaluation methodology fourth, chunking fifth. Reranking is not your main issue. Routing and fallback are also hurting you, but mostly because they are compensating for weak retrieval semantics.

Logical flaws:
The system supports max_retrieval_attempts=3, but only attempt 2 has meaningful retry planning; attempt 3 is mostly theater. See config.py, retrieve.py.
Retrieval-plan fields like parent/neighbor expansion exist but are not executed anywhere, so the architecture advertises capabilities it does not have. See schemas.py, retrieval_planner.py.
Qdrant score handling is wrong for similarity search and becomes dangerous if you ever switch away from RRF or fall back to identity ranking. See qdrant_client.py.

Heuristics are masking deeper problems in three places. extra_bm25 and ensure_doc_types are compensating for poor query-to-doc-type mapping, not solving it. PASS_WEAK is compensating for insufficient evidence rather than improving evidence acquisition. The reviewer’s post-hoc citation heuristics are compensating for generation that is allowed to overproduce claims. See retrieval.py, decision_router.py, reviewer.py.

The largest accuracy gains will come from making QuerySpec actually authoritative: emit real retrieval_profile, real hard requirements, and explicit doc-type priors; remove duplicated fallback logic once that works. After that, fix evidence representation so reranker/evidence selector operate on full, consistent chunk text, not OpenSearch snippets mixed with vector full text. Then delete one of evidence_selector or chunk_filter; you do not need both LLM stages.

Priority order by impact:

Fix LLM cache key corruption.
Make normalizer produce strict retrieval semantics and safety constraints.
Collapse query rewrite/planning into one place.
Use full chunk text for rerank/evidence selection; keep snippets only for UI/debug.
Remove decision-router gray-zone override or give it actual evidence text.
Add real neighbor/parent retrieval instead of fake plan flags.
Replace mocked integration confidence with offline evals on real query-evidence-answer triples.
Low-complexity, high-ROI changes:
Fix the cache key bug in llm_gateway.py.
Stop forcing required_evidence to soft and stop defaulting everything to generic_profile in normalizer.py.
Remove chunk_filter or disable it; it is a second LLM relevance pass after evidence_selector and mostly adds latency/variance. See chunk_filter.py, evidence_selector.py.
Keep full chunk text through rerank. Do not rerank snippets.

RL will not meaningfully improve this system yet. Your failure mode is not “the model needs better policy optimization”; it is “the architecture feeds inconsistent evidence and conflicting control signals into too many cheap LLM gates.” Without a clean reward function and a real eval harness, RL will mostly optimize noise.

Before RL, implement:
strict QuerySpec,
consistent retrieval/evidence objects,
one retrieval planner,
one evidence sufficiency gate,
one answer reviewer,
real offline evaluation.
Right now you have multiple judges and no trustworthy court.

Redesigned architecture:
User query -> normalizer outputs intent, risk, hard_requirements, retrieval_profile, doc_type_prior, rewrite_candidates.
Retrieval attempt 1 -> BM25 + vector on full chunks, doc-type filtered by plan, rerank on full text.
Evidence sufficiency gate -> single strict gate using evidence text plus hard requirements.
If fail -> targeted retry planner using missing requirements and coverage gaps.
Retrieval attempt 2 -> broaden/narrow based on missing requirements, with real neighbor/parent expansion.
If pass -> generation with bounded lane from gate.
Reviewer -> claim/citation validation only; no re-deciding retrieval semantics.
Terminal decision -> PASS / ASK_USER / ESCALATE.

The repo gives me no trustworthy live accuracy, hit-rate, escalation, latency, or cost baseline. The dashboard just exposes raw Prometheus samples, and the integration tests are heavily mocked rather than retrieval-grounded. See dashboard.py, test_rag_integration.py, test_rag_integration.py.



