"""ASSESS_EVIDENCE phase: quality gate. LLM-only."""

from app.services.evidence_quality import evaluate_quality, passes_quality_gate
from app.services.flow_debug import _pipeline_log
from app.services.orchestrator import OrchestratorContext, PhaseResult


async def execute_assess_evidence(ctx: OrchestratorContext) -> PhaseResult:
    """Run quality gate on retrieved evidence. LLM evaluates; no rule-based logic."""
    required_evidence = ctx.extra.get("active_required_evidence") or ctx.extra.get("required_evidence", [])
    hard_requirements = ctx.extra.get("active_hard_requirements") or ctx.extra.get("hard_requirements", [])
    product_type = ""
    if ctx.query_spec and getattr(ctx.query_spec, "resolved_slots", None):
        product_type = str((ctx.query_spec.resolved_slots or {}).get("product_type", "")).strip()
    quality_context = {
        "answer_shape": (
            ctx.extra.get("active_answer_shape")
            or (getattr(ctx.query_spec, "answer_shape", "direct_lookup") if ctx.query_spec else "direct_lookup")
        ),
        "evidence_families": (
            list(ctx.extra.get("active_evidence_families") or [])
            or list(getattr(ctx.query_spec, "evidence_families", None) or [])
        ),
        "active_hypothesis_name": ctx.extra.get("active_hypothesis_name"),
        "retrieval_profile": getattr(ctx.retrieval_plan, "profile", None),
        "evidence_set_uncovered_requirements": (
            list(getattr(ctx.evidence_set, "uncovered_requirements", None) or [])
            if ctx.evidence_set
            else []
        ),
        "candidate_doc_type_counts": (
            dict(getattr(ctx.candidate_pool, "doc_type_counts", None) or {})
            if ctx.candidate_pool
            else {}
        ),
    }

    quality_report = await evaluate_quality(
        ctx.effective_query or ctx.query,
        ctx.evidence,
        required_evidence,
        hard_requirements=hard_requirements,
        product_type=product_type or None,
        conversation_history=ctx.conversation_history or None,
        context=quality_context,
    )
    try:
        from app.core.metrics import evidence_quality_score
        evidence_quality_score.observe(quality_report.quality_score)
    except Exception:
        pass
    gate_passed = passes_quality_gate(
        quality_report,
        required_evidence,
        hard_requirements=hard_requirements,
    )
    _pipeline_log(
        "assess", "done",
        passes_quality_gate=gate_passed,
        quality_score=quality_report.quality_score,
        completeness_score=quality_report.completeness_score,
        actionability_score=quality_report.actionability_score,
        missing_signals=quality_report.missing_signals,
        hard_requirement_coverage=quality_report.hard_requirement_coverage,
        active_hypothesis=ctx.extra.get("active_hypothesis_name"),
        trace_id=ctx.trace_id,
    )
    history = list(ctx.extra.get("hypothesis_history", []))
    if history:
        history[-1]["quality_score"] = quality_report.quality_score
        history[-1]["gate_pass"] = gate_passed
        history[-1]["reason"] = quality_report.reason
        ctx.extra["hypothesis_history"] = history
    return PhaseResult(
        quality_report=quality_report,
        passes_quality_gate=gate_passed,
    )
