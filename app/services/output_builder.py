"""Build AnswerOutput for terminal orchestrator actions."""

import hashlib

from app.core.config import get_settings
from app.core.metrics import compute_message_cost
from app.core.tracing import llm_call_log_var, llm_usage_var
from app.services.answer_utils import render_calibrated_candidate
from app.services.flow_debug import build_flow_debug
from app.services.final_polish import polish as final_polish
from app.services.archi_config import get_final_polish_enabled, get_page_kind_filter_enabled
from app.services.orchestrator import OrchestratorAction, OrchestratorContext
from app.services.schemas import AnswerOutput


def _format_target_label(query_spec) -> str:
    slots = getattr(query_spec, "resolved_slots", None) or {}
    product_type = str(slots.get("product_type", "") or "").strip().lower()
    os_name = str(slots.get("os", "") or "").strip().lower()
    if product_type == "vps" and os_name:
        return f"{os_name.title()} VPS"
    target_entity = str(getattr(query_spec, "target_entity", "") or "").strip()
    if target_entity:
        text = target_entity.replace("_", " ").strip()
        if text and "availability by location" not in text.lower():
            return text
    for entity in list(getattr(query_spec, "entities", None) or []):
        text = str(entity or "").strip()
        lowered = text.lower()
        if text and any(token in lowered for token in ("vps", "server", "hosting", "proxy")):
            return text
    return "this service"


def _is_availability_gap_query(query_spec) -> bool:
    if not query_spec:
        return False
    answer_type = str(getattr(query_spec, "answer_type", "") or "").strip().lower()
    if answer_type in {"policy", "direct_link", "account", "clarification"}:
        return False
    answer_shape = str(getattr(query_spec, "answer_shape", "") or "").strip().lower()
    families = {
        str(item or "").strip().lower()
        for item in (getattr(query_spec, "evidence_families", None) or [])
        if str(item or "").strip()
    }
    return answer_shape == "yes_no" and (
        "capability_availability" in families or answer_type == "general"
    )


def _build_bounded_availability_gap_answer(ctx: OrchestratorContext) -> tuple[str, list[str]] | None:
    if not _is_availability_gap_query(ctx.query_spec):
        return None
    target = _format_target_label(ctx.query_spec)
    if ctx.evidence:
        answer = (
            f"I couldn't confirm from our docs whether {target} is available for that location. "
            f"I found related {target} documentation, but nothing that explicitly confirms this location."
        )
    else:
        answer = (
            f"I couldn't find documentation confirming whether {target} is available for that location."
        )
    followup = ["If you want, ask for a specific plan or location page and I'll check the closest matching docs."]
    return answer, followup


async def build_output(
    ctx: OrchestratorContext,
    action: OrchestratorAction,
    *,
    get_model_for_query,
) -> AnswerOutput:
    """Build AnswerOutput for terminal actions (DONE, ASK_USER, ESCALATE)."""
    settings = get_settings()
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

    def _is_shadow_enabled() -> bool:
        if bool(getattr(settings, "soft_contract_enabled", True)):
            return False
        pct = int(getattr(settings, "soft_contract_shadow_percent", 0) or 0)
        if pct <= 0:
            return False
        stable_key = f"{ctx.trace_id or ''}:{ctx.query or ''}".strip() or "default"
        digest = hashlib.sha256(stable_key.encode("utf-8")).hexdigest()
        bucket = int(digest[:8], 16) % 100
        return bucket < pct

    rollout_debug = {
        "soft_contract_enabled": bool(getattr(settings, "soft_contract_enabled", True)),
        "answer_candidate_enabled": bool(getattr(settings, "answer_candidate_enabled", True)),
        "page_kind_filter_enabled": get_page_kind_filter_enabled(),
        "targeted_retry_enabled": bool(getattr(settings, "targeted_retry_enabled", True)),
        "soft_contract_shadow_active": _is_shadow_enabled(),
    }

    if action == OrchestratorAction.DONE:
        answer = ctx.answer
        followup = list(ctx.followup or [])
        if (
            bool(getattr(settings, "soft_contract_enabled", True))
            and bool(getattr(settings, "answer_candidate_enabled", True))
        ):
            candidate = (
                dict(extra.get("answer_candidate"))
                if isinstance(extra.get("answer_candidate"), dict)
                else None
            )
            calibrated_lane = (
                getattr(ctx.review_result, "final_lane", None)
                if ctx.review_result
                else ctx.answer_lane
            )
            answer, followup = render_calibrated_candidate(
                candidate,
                calibrated_lane=calibrated_lane,
                fallback_answer=answer,
                fallback_followup=followup,
            )
            extra["candidate_render_applied"] = bool(candidate)
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
        debug_payload = build_flow_debug(
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
            hypothesis_judge=ctx.hypothesis_judge,
            conversation_relevance=ctx.extra.get("conversation_relevance"),
        )
        debug_payload["rollout_flags"] = rollout_debug
        return AnswerOutput(
            decision="PASS",
            answer=answer,
            followup_questions=followup,
            citations=ctx.citations,
            confidence=ctx.confidence,
            debug=debug_payload,
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
        debug_payload = build_flow_debug(
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
            hypothesis_judge=ctx.hypothesis_judge,
            conversation_relevance=ctx.extra.get("conversation_relevance"),
        )
        debug_payload["rollout_flags"] = rollout_debug
        return AnswerOutput(
            decision="ESCALATE",
            answer=escalate_answer,
            followup_questions=[],
            citations=ctx.citations,
            confidence=ctx.confidence,
            debug=debug_payload,
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
            debug_payload = build_flow_debug(
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
                hypothesis_judge=ctx.hypothesis_judge,
                conversation_relevance=ctx.extra.get("conversation_relevance"),
            )
            debug_payload["rollout_flags"] = rollout_debug
            return AnswerOutput(
                decision=dr.decision,
                answer=dr.answer,
                followup_questions=dr.clarifying_questions,
                citations=[],
                confidence=0.0,
                debug=debug_payload,
            )
        no_evidence = not evidence
        max_reached = not ctx.can_retry()
        bounded_gap = _build_bounded_availability_gap_answer(ctx) if max_reached else None
        no_evidence_msg = (
            bounded_gap[0]
            if bounded_gap
            else (
                "We couldn't find relevant information in our knowledge base. "
                "Could you rephrase your question or provide more context?"
            )
        )
        default_answer = (
            no_evidence_msg
            if no_evidence
            else (ctx.answer or (bounded_gap[0] if bounded_gap else "We need more information to help. Could you clarify your question?"))
        )
        default_followup = (
            (bounded_gap[1] if bounded_gap else ["What specific topic are you asking about?"])
            if no_evidence
            else (ctx.followup or (bounded_gap[1] if bounded_gap else ["What specifically would you like to know?"]))
        )
        debug_payload = build_flow_debug(
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
            hypothesis_judge=ctx.hypothesis_judge,
            conversation_relevance=ctx.extra.get("conversation_relevance"),
        )
        debug_payload["rollout_flags"] = rollout_debug
        return AnswerOutput(
            decision="ASK_USER",
            answer=default_answer,
            followup_questions=default_followup,
            citations=ctx.citations,
            confidence=ctx.confidence,
            debug=debug_payload,
        )

    out = AnswerOutput(
        decision="ASK_USER",
        answer="We need more information to help.",
        followup_questions=[],
        citations=[],
        confidence=0.0,
        debug={"trace_id": ctx.trace_id, "stage_reasons": ctx.stage_reasons, "termination_reason": ctx.termination_reason},
    )
    out.debug["rollout_flags"] = rollout_debug
    return out
