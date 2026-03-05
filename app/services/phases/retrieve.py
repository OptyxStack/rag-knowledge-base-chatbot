"""RETRIEVE phase: hybrid retrieval with evidence hygiene."""

from typing import Any

from app.services.evidence_hygiene import compute_hygiene
from app.services.flow_debug import _pipeline_log
from app.services.evidence_evaluator import evaluate_evidence
from app.services.retry_planner import plan_retry
from app.services.orchestrator import OrchestratorContext, PhaseResult
from app.services.answer_utils import resolve_retrieval_query
from app.services.archi_config import get_evidence_evaluator_enabled


async def execute_retrieve(
    ctx: OrchestratorContext,
    *,
    retrieval,
    orchestrator,
    settings,
) -> PhaseResult:
    """Run retrieval for current attempt."""
    attempt = ctx.retrieval_attempt + 1
    _pipeline_log("retrieve", "start", attempt=attempt, query=ctx.effective_query[:80], trace_id=ctx.trace_id)
    required_evidence = ctx.extra.get("required_evidence", [])
    retrieval_profile = ctx.extra.get("retrieval_profile", "generic_profile")

    retry_strategy = None
    if attempt == 2 and ctx.quality_report:
        evidence_eval = ctx.extra.get("evidence_eval_result")
        retry_strategy = plan_retry(
            ctx.quality_report.missing_signals if ctx.quality_report else [],
            2,
            evidence_eval_result=evidence_eval,
            query_spec=ctx.query_spec,
        )

    retrieval_query, _, rewrite_candidates = resolve_retrieval_query(
        base_query=ctx.effective_query,
        attempt=attempt,
        query_spec=ctx.query_spec,
        retry_strategy=retry_strategy,
        explicit_override=ctx.retry_query_override,
    )
    ctx.retry_query_override = None

    retry_strategy_applied: dict[str, Any] = {
        "retrieval_profile": retrieval_profile,
        "selected_retrieval_query": retrieval_query,
        "rewrite_candidates": rewrite_candidates[:3],
    }
    if retry_strategy:
        retry_strategy_applied.update({
            "boost_patterns": (retry_strategy.boost_patterns or [])[:5],
            "filter_doc_types": retry_strategy.filter_doc_types,
            "suggested_query": retry_strategy.suggested_query,
        })
    ctx.extra["retry_strategy_applied"] = retry_strategy_applied

    evidence_pack = await retrieval.retrieve(
        retrieval_query,
        conversation_history=ctx.conversation_history,
        retry_strategy=retry_strategy,
        attempt=attempt,
        query_spec=ctx.query_spec,
    )
    evidence = evidence_pack.chunks

    stats = evidence_pack.retrieval_stats or {}
    _pipeline_log(
        "retrieve", "done",
        chunks=len(evidence),
        bm25_count=stats.get("bm25_count"),
        vector_count=stats.get("vector_count"),
        merged_count=stats.get("merged_count"),
        reranked_count=stats.get("reranked_count"),
        query_rewrite=stats.get("query_rewrite"),
        trace_id=ctx.trace_id,
    )

    if evidence:
        if get_evidence_evaluator_enabled():
            ctx.extra["evidence_eval_result"] = await evaluate_evidence(
                ctx.effective_query,
                ctx.query_spec,
                evidence,
                top_n=5,
            )
        hygiene = compute_hygiene(evidence)
        if evidence_pack.retrieval_stats:
            evidence_pack.retrieval_stats["evidence_signatures"] = {
                "pct_chunks_with_url": round(hygiene.pct_chunks_with_url, 1),
                "pct_chunks_with_number_unit": round(hygiene.pct_chunks_with_number_unit, 1),
                "pct_chunks_boilerplate_gt_06": round(hygiene.pct_chunks_boilerplate_gt_06, 1),
                "median_content_density": round(hygiene.median_content_density, 3),
            }

    return PhaseResult(
        evidence_pack=evidence_pack,
        evidence=evidence,
    )
