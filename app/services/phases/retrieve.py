"""RETRIEVE phase: hybrid retrieval with evidence hygiene."""

from typing import Any

from app.services.evidence_hygiene import compute_hygiene
from app.services.flow_debug import _pipeline_log
from app.services.evidence_evaluator import evaluate_evidence
from app.services.retry_planner import plan_retry
from app.services.retrieval_planner import build_retrieval_plan_for_attempt
from app.services.orchestrator import OrchestratorContext, PhaseResult
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

    retry_strategy = None
    if attempt > 1:
        evidence_eval = ctx.extra.get("evidence_eval_result")
        missing_signals = (
            list(ctx.quality_report.missing_signals)
            if ctx.quality_report and getattr(ctx.quality_report, "missing_signals", None)
            else ["missing_evidence"]
        )
        retry_strategy = plan_retry(
            missing_signals,
            attempt,
            evidence_eval_result=evidence_eval,
            query_spec=ctx.query_spec,
        )

    retrieval_plan, planning_debug = await build_retrieval_plan_for_attempt(
        base_query=ctx.effective_query,
        attempt=attempt,
        query_spec=ctx.query_spec,
        retry_strategy=retry_strategy,
        explicit_override=ctx.retry_query_override,
        conversation_history=ctx.conversation_history,
    )
    ctx.retry_query_override = None

    retry_strategy_applied: dict[str, Any] = {
        "retrieval_profile": retrieval_plan.profile,
        "selected_retrieval_query": planning_debug.get("selected_retrieval_query", ctx.effective_query),
        "rewrite_candidates": planning_debug.get("rewrite_candidates", []),
        "query_source": planning_debug.get("query_source"),
    }
    if retry_strategy:
        retry_strategy_applied.update({
            "boost_patterns": (retry_strategy.boost_patterns or [])[:5],
            "filter_doc_types": retry_strategy.filter_doc_types,
            "suggested_query": retry_strategy.suggested_query,
            "hypothesis_name": retry_strategy.hypothesis_name,
            "hypothesis_index": retry_strategy.hypothesis_index,
        })
    ctx.extra["retry_strategy_applied"] = retry_strategy_applied
    ctx.extra["active_required_evidence"] = list(retrieval_plan.active_required_evidence or [])
    ctx.extra["active_hard_requirements"] = list(retrieval_plan.active_hard_requirements or [])
    ctx.extra["active_soft_requirements"] = list(retrieval_plan.active_soft_requirements or [])
    ctx.extra["active_hypothesis_name"] = retrieval_plan.active_hypothesis_name
    ctx.extra["active_answer_shape"] = retrieval_plan.answer_shape
    ctx.extra["active_evidence_families"] = list(retrieval_plan.evidence_families or [])

    evidence_pack = await retrieval.retrieve(
        ctx.effective_query,
        conversation_history=ctx.conversation_history,
        retry_strategy=retry_strategy,
        attempt=attempt,
        query_spec=ctx.query_spec,
        retrieval_plan=retrieval_plan,
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
    history = list(ctx.extra.get("hypothesis_history", []))
    history.append({
        "name": retrieval_plan.active_hypothesis_name,
        "retrieval_profile": retrieval_plan.profile,
        "evidence_families": list(retrieval_plan.evidence_families or []),
        "required_evidence": list(retrieval_plan.active_required_evidence or []),
        "hard_requirements": list(retrieval_plan.active_hard_requirements or []),
        "evidence_count": len(evidence),
    })
    ctx.extra["hypothesis_history"] = history

    return PhaseResult(
        evidence_pack=evidence_pack,
        evidence=evidence,
    )
