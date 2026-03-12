"""Flow debug utilities for RAG pipeline inspection."""

from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk

logger = get_logger(__name__)


def _pipeline_log(stage: str, event: str, **kwargs: Any) -> None:
    """Log pipeline stage if pipeline_logging_enabled. Use for tracing RAG flow."""
    if not getattr(get_settings(), "pipeline_logging_enabled", True):
        return
    # Filter None, truncate long strings for readability
    safe: dict[str, Any] = {"stage": stage, "pipeline_event": event}
    for k, v in kwargs.items():
        if v is None:
            continue
        if isinstance(v, str) and len(v) > 200:
            safe[k] = v[:200] + "..."
        else:
            safe[k] = v
    logger.info("pipeline", **safe)
from app.services.retrieval import EvidencePack
from app.services.schemas import AnswerPlan, DecisionResult, QuerySpec

# Human-readable mapping for decision_router.reason (explainability)
REASON_HUMAN_READABLE: dict[str, str] = {
    "sufficient": "Evidence sufficient for answer",
    "partial_sufficient": "Partial evidence; bounded answer allowed",
    "missing_constraints": "User constraints missing; need clarification",
    "missing_evidence_quality": "Evidence quality below threshold",
    "ambiguous_query": "Query ambiguous; referent unclear",
    "high_risk_insufficient": "High-risk query with insufficient evidence",
}


def build_flow_debug(
    *,
    trace_id: str | None,
    evidence_pack: EvidencePack | None,
    evidence: list[EvidenceChunk],
    messages: list[dict[str, str]],
    model_used: str,
    llm_tokens: dict[str, int] | None = None,
    cost_usd: float | None = None,
    llm_usage_breakdown: list[dict] | None = None,
    llm_call_log: list[dict] | None = None,
    attempt: int = 1,
    reviewer_reasons: list[str] | None = None,
    max_attempts_reached: bool = False,
    finish_reason: str | None = None,
    quality_report: Any = None,
    retry_strategy_applied: dict[str, Any] | None = None,
    query_spec: QuerySpec | None = None,
    decision_router: DecisionResult | None = None,
    source_lang: str | None = None,
    evidence_eval_result: dict[str, Any] | None = None,
    self_critic_regenerated: bool = False,
    final_polish_applied: bool = False,
    answer_plan: AnswerPlan | None = None,
    review_result: Any = None,
    stage_reasons: list[str] | None = None,
    termination_reason: str | None = None,
    hypothesis_judge: dict[str, Any] | None = None,
    conversation_relevance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build debug dict for flow inspection (internal admin)."""
    debug: dict[str, Any] = {
        "trace_id": trace_id,
        "attempt": attempt,
        "model_used": model_used,
    }
    if stage_reasons:
        debug["stage_reasons"] = stage_reasons
    if termination_reason:
        debug["termination_reason"] = termination_reason
    if evidence_pack:
        debug["retrieval_stats"] = evidence_pack.retrieval_stats
        qr = evidence_pack.retrieval_stats.get("query_rewrite")
        if qr:
            debug["query_rewrite"] = qr
        if getattr(evidence_pack, "retrieval_plan", None):
            rp = evidence_pack.retrieval_plan
            debug["retrieval_plan"] = {
                "profile": rp.profile,
                "attempt_index": rp.attempt_index,
                "reason": rp.reason,
                "fetch_n": rp.fetch_n,
                "rerank_k": rp.rerank_k,
            }
        if getattr(evidence_pack, "evidence_set", None):
            es = evidence_pack.evidence_set
            debug["evidence_set"] = {
                "covered_requirements": es.covered_requirements,
                "uncovered_requirements": es.uncovered_requirements,
                "build_reason": es.build_reason,
                "diversity_score": es.diversity_score,
            }
    if evidence:
        debug["evidence_summary"] = [
            {
                "chunk_id": e.chunk_id,
                "source_url": e.source_url,
                "doc_type": e.doc_type,
                "score": getattr(e, "score", None),
                "snippet": (e.snippet or (e.full_text or "")[:200]) + ("..." if len((e.full_text or "")) > 200 else ""),
            }
            for e in evidence
        ]
    if messages:
        system = next((m["content"] for m in messages if m.get("role") == "system"), "")
        user = next((m["content"] for m in messages if m.get("role") == "user"), "")
        debug["prompt_preview"] = {
            "system_length": len(system),
            "user_length": len(user),
            "system_preview": system,
            "user_preview": user,
        }
    if llm_tokens:
        debug["llm_tokens"] = llm_tokens
    if cost_usd is not None:
        debug["cost_usd"] = round(cost_usd, 6)
    if llm_usage_breakdown:
        debug["llm_usage_breakdown"] = llm_usage_breakdown
    if llm_call_log:
        debug["llm_call_log"] = llm_call_log
    if reviewer_reasons:
        debug["reviewer_reasons"] = reviewer_reasons
    if max_attempts_reached:
        debug["max_attempts_reached"] = True
    if finish_reason:
        debug["finish_reason"] = finish_reason
    if quality_report:
        debug["quality_report"] = {
            "quality_score": quality_report.quality_score,
            "feature_scores": quality_report.feature_scores,
            "missing_signals": quality_report.missing_signals,
            "hard_requirement_coverage": getattr(
                quality_report, "hard_requirement_coverage", None
            ),
        }
    if retry_strategy_applied:
        debug["retry_strategy"] = retry_strategy_applied
    if query_spec:
        debug["query_spec"] = {
            "intent": query_spec.intent,
            "risk_level": query_spec.risk_level,
            "is_ambiguous": query_spec.is_ambiguous,
            "required_evidence": query_spec.required_evidence,
            "canonical_query_en": query_spec.canonical_query_en,
            "entities": query_spec.entities,
            "resolved_slots": query_spec.resolved_slots,
            "config_overrides_applied": query_spec.config_overrides_applied,
            "extraction_mode": getattr(query_spec, "extraction_mode", None),
        }
    if decision_router:
        reason = decision_router.reason
        debug["decision_router"] = {
            "decision": decision_router.decision,
            "reason": reason,
            "reason_human": REASON_HUMAN_READABLE.get(
                reason, reason.replace("_", " ").title()
            ),
            "lane": decision_router.resolved_lane(),
            "answer_policy": decision_router.answer_policy,
        }
    if answer_plan:
        debug["answer_plan"] = {
            "lane": answer_plan.lane,
            "allowed_claim_scope": answer_plan.allowed_claim_scope,
            "tone_policy": answer_plan.tone_policy,
            "output_blocks": answer_plan.output_blocks,
        }
    if review_result and getattr(review_result, "unsupported_claims", None):
        debug["review_unsupported_claims"] = review_result.unsupported_claims[:5]
    if review_result and getattr(review_result, "claim_to_citation_map", None):
        c2c = review_result.claim_to_citation_map
        debug["claim_to_citation_map"] = {
            k: v[:3] for k, v in list(c2c.items())[:10]
        }
    if review_result and getattr(review_result, "status", None) in ("trim_unsupported_claims", "downgrade_lane"):
        debug["review_action"] = review_result.status
    if source_lang:
        debug["source_lang"] = source_lang
    if evidence_eval_result:
        debug["evidence_eval"] = evidence_eval_result
    if self_critic_regenerated:
        debug["self_critic_regenerated"] = True
    if final_polish_applied:
        debug["final_polish_applied"] = True
    if hypothesis_judge:
        debug["hypothesis_judge"] = hypothesis_judge
    if conversation_relevance:
        debug["conversation_relevance"] = conversation_relevance
    return debug
