Root Causes

Control-plane weakness: QuerySpec is not authoritative, so retrieval semantics are re-derived in multiple places with conflicting heuristics. See normalizer.py, retrieval_planner.py, retrieval.py.
Evidence inconsistency: BM25 passes snippets while vector passes full chunks, so merge, rerank, and later LLM judges are not scoring the same object. See opensearch_client.py, qdrant_client.py.
Too many LLM judges: normalizer, evidence quality, evidence selector, chunk filter, decision router, optional self-critic. That creates latency and non-deterministic state transitions more than it improves quality. See model_router.py.
Unsafe flow overrides: generator output is not the real decision boundary; later stages hardcode or coerce PASS. See verify.py, answer_utils.py.
Reliability bug in shared infrastructure: Redis cache key corruption can return the wrong LLM output for unrelated prompts. See llm_gateway.py.
Weak eval discipline: current tests mostly mock success paths, so regressions in retrieval quality and grounding are largely invisible. See test_rag_integration.py.
Phase 1

Fix LLM cache key bug.
Why: this is a correctness issue, not an optimization.
Impact: large stability gain, medium accuracy gain.
Complexity: low.
Risk: low; mostly cache invalidation and rollout coordination.
Stop overriding prompt hash with app-level cache key; keep prompt cache as provider metadata only.
Why: separates Redis memoization from provider-side prompt caching.
Impact: eliminates cross-query contamination.
Complexity: low.
Risk: temporary cache miss spike.
Remove any path that hardcodes reviewer input as decision="PASS".
Why: current verification ignores upstream control flow.
Impact: medium stability, medium safety.
Complexity: low.
Risk: short-term increase in ASK_USER until retrieval improves.
Add canary logging for cache hits, prompt hashes, and decision transitions.
Why: you need to detect silent corruption and routing drift immediately.
Impact: medium observability, low direct accuracy.
Complexity: low.
Risk: low.
Phase 2

Make QuerySpec authoritative for retrieval_profile, hard_requirements, doc_type_prior, and retry rewrites.
Why: this removes policy fragmentation at the source.
Impact: large retrieval precision and consistency gain.
Complexity: medium.
Risk: if prompts are weak, you can regress broadly; gate with shadow evaluation.
Collapse retrieval planning into one module.
Why: today planning lives in normalizer, planner, retrieval service, and answer_utils.
Impact: large stability gain, medium accuracy gain.
Complexity: medium.
Risk: migration bugs if legacy fallbacks are removed too quickly.
Use full chunk text through retrieval, rerank, and evidence selection; keep snippets only for UI/debug.
Why: ranking and evidence scoring must operate on the same representation.
Impact: medium to large accuracy gain.
Complexity: medium.
Risk: token and latency increase; mitigate with tighter rerank candidate limits.
Revisit chunking with smaller, cleaner semantic units plus optional parent references.
Why: current 300-700 token chunks are broad for policy and troubleshooting facts. See ingestion.py.
Impact: medium recall and citation precision gain.
Complexity: medium.
Risk: reindexing required.
Phase 3

Remove chunk_filter entirely once evidence selection is reliable.
Why: it is a second LLM relevance pass over already selected evidence.
Impact: medium stability and latency gain, small accuracy gain.
Complexity: low.
Risk: slight prompt bloat until selector is tuned.
Keep one pre-generation gate and one post-generation gate.
Why: you need fewer judges with clearer contracts.
Impact: large simplification, medium accuracy gain.
Complexity: medium.
Risk: some edge cases may move from bounded answers to ASK_USER.
Restrict decision router to ambiguity/risk routing only; do not let it second-guess evidence sufficiency if the evidence gate already failed.
Why: current gray-zone override is too weakly grounded. See decision_router.py.
Impact: medium safety and consistency gain.
Complexity: low.
Risk: slightly higher deferral rate at first.
Make reviewer purely claim/citation validation, not retrieval policy repair.
Why: reviewer should validate grounding, not compensate for poor retrieval.
Impact: medium stability.
Complexity: medium.
Risk: surfaces upstream weaknesses more clearly.
Phase 4

Build an offline eval set from real production queries.
Why: without real query-document-answer triples, you cannot measure progress.
Impact: very large long-term quality gain.
Complexity: medium.
Risk: labeling cost.
Split evals into retrieval recall, evidence coverage, final answer correctness, and hallucination.
Why: otherwise you will not know which phase regressed.
Impact: high diagnosis value.
Complexity: medium.
Risk: none.
Add replay-based regression tests for top failure classes: ambiguous referents, policy questions, pricing questions, troubleshooting steps, multilingual queries.
Why: these are exactly where your architecture branches.
Impact: medium stability.
Complexity: medium.
Risk: none.
Keep / Modify / Remove

Keep: hybrid retrieval, reranker, reviewer concept, orchestrator, ingestion pipeline, doc-type metadata, retry mechanism.
Modify: normalizer, retrieval planner, retrieval service, evidence quality gate, reviewer, output builder, chunking strategy, observability.
Remove entirely: chunk_filter; decision-router gray-zone ASK_USER -> PASS override; duplicated query rewrite logic in multiple layers; fake plan flags that are not executed.
Target Architecture
User Query -> Normalizer (intent, risk, hard_requirements, retrieval_profile, doc_type_prior, rewrite_candidates) -> Retrieval Plan -> Hybrid Retrieval on full chunks -> Reranker -> Evidence Selector -> Evidence Sufficiency Gate -> Generate -> Claim/Citation Reviewer -> PASS / ASK_USER / ESCALATE

Retry path:
Evidence gate fail -> targeted retry planner -> second retrieval attempt -> same evidence gate.
No extra chunk filter. No router overriding evidence decisions.

Metrics

Retrieval recall@k on labeled gold chunks.
Evidence coverage rate for required fields.
Answer correctness rate.
Hallucination rate.
Citation validity rate.
Unsupported-claim trim rate.
ASK_USER rate by intent.
ESCALATE rate by risk class.
Cache collision rate and wrong-response replay count.
P50/P95 latency by stage.
Cost per successful grounded answer.
Realistic Improvement Range

After Phase 1: 10-20% reduction in unstable/wrong-path behavior, mainly from cache and flow fixes.
After Phase 2: 8-18 point gain in retrieval recall on hard queries, 5-12 point gain in grounded answer accuracy.
After Phase 3: 20-35% latency reduction and noticeably lower routing variance.
After Phase 4: not immediate user-facing accuracy, but much faster and safer iteration velocity.