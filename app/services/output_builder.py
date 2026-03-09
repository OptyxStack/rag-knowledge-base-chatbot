"""Build AnswerOutput for terminal orchestrator actions."""

from app.core.metrics import compute_message_cost
from app.core.tracing import llm_call_log_var, llm_usage_var
from app.services.flow_debug import build_flow_debug
from app.services.final_polish import polish as final_polish
from app.services.archi_config import get_final_polish_enabled
from app.services.orchestrator import OrchestratorAction, OrchestratorContext
from app.services.schemas import AnswerOutput


async def build_output(
    ctx: OrchestratorContext,
    action: OrchestratorAction,
    *,
    get_model_for_query,
) -> AnswerOutput:
    """Build AnswerOutput for terminal actions (DONE, ASK_USER, ESCALATE)."""
    extra = ctx.extra
    usage_list = llm_usage_var.get() or []
    llm_call_log = llm_call_log_var.get() or []
    cost_usd, agg_tokens, usage_breakdown = compute_message_cost(usage_list)
    llm_resp = extra.get("llm_resp")
    llm_tokens_for_debug = (
        agg_tokens if (agg_tokens["input"] or agg_tokens["output"]) else
        ({"input": llm_resp.input_tokens, "output": llm_resp.output_tokens} if llm_resp else None)
    )
    evidence_pack = ctx.evidence_pack
    evidence = ctx.evidence
    messages = extra.get("messages", [])
    llm_resp = extra.get("llm_resp")
    retry_strategy_applied = extra.get("retry_strategy_applied")
    evidence_eval = extra.get("evidence_eval_result")
    self_critic_regenerated = extra.get("self_critic_regenerated", False)
    attempt = ctx.retrieval_attempt + 1
    model = get_model_for_query(ctx.query)

    evidence_eval_debug = None
    if evidence_eval:
        evidence_eval_debug = {
            "relevance_score": getattr(evidence_eval, "relevance_score", None),
            "retry_needed": getattr(evidence_eval, "retry_needed", None),
            "coverage_gaps": getattr(evidence_eval, "coverage_gaps", [])[:3],
        }

    if action == OrchestratorAction.DONE:
        answer = ctx.answer
        if get_final_polish_enabled():
            polished = await final_polish(answer)
            if polished:
                answer = polished
                extra["final_polish_applied"] = True
        try:
            from app.core.metrics import decision_total
            decision_total.labels(decision="PASS").inc()
        except Exception:
            pass
        return AnswerOutput(
            decision="PASS",
            answer=answer,
            followup_questions=[],
            citations=ctx.citations,
            confidence=ctx.confidence,
            debug=build_flow_debug(
                trace_id=ctx.trace_id,
                evidence_pack=evidence_pack,
                evidence=evidence,
                messages=messages,
                model_used=model,
                llm_tokens=llm_tokens_for_debug,
                cost_usd=cost_usd if cost_usd > 0 else None,
                llm_usage_breakdown=usage_breakdown if usage_breakdown else None,
                llm_call_log=llm_call_log if llm_call_log else None,
                attempt=attempt,
                finish_reason=getattr(llm_resp, "finish_reason", None) if llm_resp else None,
                quality_report=ctx.quality_report,
                retry_strategy_applied=retry_strategy_applied,
                query_spec=ctx.query_spec,
                decision_router=ctx.decision_result,
                source_lang=ctx.source_lang,
                evidence_eval_result=evidence_eval_debug,
                self_critic_regenerated=self_critic_regenerated,
                final_polish_applied=extra.get("final_polish_applied", False),
                answer_plan=ctx.answer_plan,
                review_result=ctx.review_result,
                stage_reasons=ctx.stage_reasons,
                termination_reason=ctx.termination_reason,
            ),
        )

    if action == OrchestratorAction.ESCALATE:
        try:
            from app.core.metrics import decision_total, escalation_rate
            decision_total.labels(decision="ESCALATE").inc()
            escalation_rate.inc()
        except Exception:
            pass
        rr = getattr(ctx, "_last_reviewer_result", None)
        forced_handoff = bool(ctx.review_result and ctx.review_result.final_lane == "ESCALATE")
        escalate_answer = "" if forced_handoff else ctx.answer
        if extra.get("error"):
            escalate_answer = "I'm sorry, I encountered an error. Please try again or contact support."
        elif not escalate_answer:
            escalate_answer = "This request requires human review. A support agent will follow up."
        return AnswerOutput(
            decision="ESCALATE",
            answer=escalate_answer,
            followup_questions=[],
            citations=ctx.citations,
            confidence=ctx.confidence,
            debug=build_flow_debug(
                trace_id=ctx.trace_id,
                evidence_pack=evidence_pack,
                evidence=evidence,
                messages=messages,
                model_used=model,
                llm_tokens=llm_tokens_for_debug,
                cost_usd=cost_usd if cost_usd > 0 else None,
                llm_usage_breakdown=usage_breakdown if usage_breakdown else None,
                llm_call_log=llm_call_log if llm_call_log else None,
                attempt=attempt,
                reviewer_reasons=getattr(rr, "reasons", []) if rr else None,
                quality_report=ctx.quality_report,
                retry_strategy_applied=retry_strategy_applied,
                query_spec=ctx.query_spec,
                decision_router=ctx.decision_result,
                source_lang=ctx.source_lang,
                evidence_eval_result=evidence_eval_debug,
                self_critic_regenerated=self_critic_regenerated,
                answer_plan=ctx.answer_plan,
                review_result=ctx.review_result,
                stage_reasons=ctx.stage_reasons,
                termination_reason=ctx.termination_reason,
            ),
        )

    if action == OrchestratorAction.ASK_USER:
        try:
            from app.core.metrics import decision_total
            decision_total.labels(decision="ASK_USER").inc()
        except Exception:
            pass
        dr = ctx.decision_result
        rr = getattr(ctx, "_last_reviewer_result", None)
        if dr and dr.decision != "PASS":
            return AnswerOutput(
                decision=dr.decision,
                answer=dr.answer,
                followup_questions=dr.clarifying_questions,
                citations=[],
                confidence=0.0,
                debug=build_flow_debug(
                    trace_id=ctx.trace_id,
                    evidence_pack=evidence_pack,
                    evidence=evidence,
                    messages=[],
                    model_used=model,
                    llm_tokens=llm_tokens_for_debug,
                    cost_usd=cost_usd if cost_usd > 0 else None,
                    llm_usage_breakdown=usage_breakdown if usage_breakdown else None,
                    llm_call_log=llm_call_log if llm_call_log else None,
                    attempt=attempt,
                    quality_report=ctx.quality_report,
                    retry_strategy_applied=retry_strategy_applied,
                    query_spec=ctx.query_spec,
                    decision_router=dr,
                    source_lang=ctx.source_lang,
                    evidence_eval_result=evidence_eval_debug,
                    review_result=ctx.review_result,
                    stage_reasons=ctx.stage_reasons,
                    termination_reason=ctx.termination_reason,
                ),
            )
        no_evidence = not evidence
        max_reached = not ctx.can_retry()
        no_evidence_msg = (
            "I couldn't find relevant information in our knowledge base. "
            "Could you rephrase your question or provide more context?"
        )
        default_answer = no_evidence_msg if no_evidence else (ctx.answer or "I need more information to help. Could you clarify your question?")
        default_followup = ["What specific topic are you asking about?"] if no_evidence else (ctx.followup or ["What specifically would you like to know?"])
        return AnswerOutput(
            decision="ASK_USER",
            answer=default_answer,
            followup_questions=default_followup,
            citations=ctx.citations,
            confidence=ctx.confidence,
            debug=build_flow_debug(
                trace_id=ctx.trace_id,
                evidence_pack=evidence_pack,
                evidence=evidence,
                messages=messages,
                model_used=model,
                llm_tokens=llm_tokens_for_debug,
                cost_usd=cost_usd if cost_usd > 0 else None,
                llm_usage_breakdown=usage_breakdown if usage_breakdown else None,
                llm_call_log=llm_call_log if llm_call_log else None,
                attempt=attempt,
                reviewer_reasons=getattr(rr, "reasons", []) if rr else None,
                max_attempts_reached=max_reached,
                finish_reason=getattr(llm_resp, "finish_reason", None) if llm_resp else None,
                quality_report=ctx.quality_report,
                retry_strategy_applied=retry_strategy_applied,
                query_spec=ctx.query_spec,
                decision_router=dr,
                source_lang=ctx.source_lang,
                evidence_eval_result=evidence_eval_debug,
                self_critic_regenerated=self_critic_regenerated,
                answer_plan=ctx.answer_plan,
                review_result=ctx.review_result,
                stage_reasons=ctx.stage_reasons,
                termination_reason=ctx.termination_reason,
            ),
        )

    return AnswerOutput(
        decision="ASK_USER",
        answer="I need more information to help.",
        followup_questions=[],
        citations=[],
        confidence=0.0,
        debug={"trace_id": ctx.trace_id, "stage_reasons": ctx.stage_reasons, "termination_reason": ctx.termination_reason},
    )
