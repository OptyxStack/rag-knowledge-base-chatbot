"""GENERATE phase: LLM generation + optional self-critic."""

from app.core.logging import get_logger
from app.services.answer_utils import (
    apply_answer_plan,
    build_answer_plan,
    format_answer_plan_instruction,
    format_evidence_for_prompt,
    parse_llm_response,
)
from app.services.archi_config import get_self_critic_enabled
from app.services.branding_config import get_system_prompt
from app.services.conversation_context import truncate_for_prompt
from app.services.flow_debug import _pipeline_log
from app.services.orchestrator import OrchestratorContext, PhaseResult
from app.services.self_critic import critique as self_critic

logger = get_logger(__name__)


async def execute_generate(
    ctx: OrchestratorContext,
    *,
    llm,
    orchestrator,
    settings,
) -> PhaseResult:
    """Generate an answer from evidence selected by retrieval/evidence selector."""
    evidence = ctx.evidence

    answer_plan = build_answer_plan(
        ctx.decision_result,
        ctx.query_spec,
        ctx.quality_report,
    )
    max_chars = settings.llm_max_evidence_chars
    evidence_block = format_evidence_for_prompt(evidence, max_chars)
    user_content = f"User question: {ctx.effective_query}\n\nEvidence:\n{evidence_block}"
    system_prompt = get_system_prompt()
    system_prompt = (
        f"{system_prompt}\n\n"
        f"{format_answer_plan_instruction(answer_plan, ctx.quality_report)}"
    )
    messages: list[dict[str, str]] = [{"role": "system", "content": system_prompt}]
    if ctx.conversation_history:
        for msg in truncate_for_prompt(ctx.conversation_history):
            messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_content})
    ctx.extra["messages"] = messages

    model = orchestrator.get_model_for_query(ctx.query)
    _pipeline_log("generate", "start", model=model, evidence_chunks=len(ctx.evidence), trace_id=ctx.trace_id)
    try:
        from app.core.tracing import current_llm_task_var

        current_llm_task_var.set("generate")
        llm_resp = await llm.chat(
            messages=messages,
            temperature=settings.llm_temperature,
            model=model,
        )
    except Exception as e:
        logger.error("answer_llm_failed", error=str(e))
        ctx.extra["error"] = str(e)
        raise

    ctx.extra["llm_resp"] = llm_resp
    if getattr(llm_resp, "finish_reason", None) == "length":
        logger.warning("llm_response_truncated", trace_id=ctx.trace_id)

    parsed = parse_llm_response(llm_resp.content)
    decision, answer, followup, confidence = apply_answer_plan(
        answer_plan,
        parsed,
    )
    citations = parsed.get("citations", [])

    self_critic_regenerated = False
    max_gen_attempts = 1 + getattr(settings, "self_critic_regenerate_max", 1)
    for gen_attempt in range(1, max_gen_attempts + 1):
        if get_self_critic_enabled() and gen_attempt < max_gen_attempts:
            critique_result = await self_critic(
                ctx.effective_query, answer, citations, ctx.evidence
            )
            if critique_result and not critique_result.pass_:
                try:
                    from app.core.metrics import self_critic_regenerate_total

                    self_critic_regenerate_total.inc()
                except Exception:
                    pass
                logger.info("self_critic_fail", issues=critique_result.issues[:3])
                feedback = (
                    "\n\nPrevious attempt had issues: "
                    f"{', '.join(critique_result.issues[:2])}. "
                    f"Fix: {critique_result.suggested_fix}"
                )
                messages[-1]["content"] = messages[-1]["content"] + feedback
                try:
                    current_llm_task_var.set("generate_regenerate")
                    llm_resp = await llm.chat(
                        messages=messages,
                        temperature=settings.llm_temperature,
                        model=model,
                    )
                    parsed = parse_llm_response(llm_resp.content)
                    decision, answer, followup, confidence = apply_answer_plan(
                        answer_plan, parsed
                    )
                    citations = parsed.get("citations", [])
                except Exception as err:
                    logger.warning("self_critic_regenerate_failed", error=str(err))
                self_critic_regenerated = True
                ctx.extra["llm_resp"] = llm_resp
        break
    ctx.extra["self_critic_regenerated"] = self_critic_regenerated

    _pipeline_log(
        "generate",
        "done",
        confidence=confidence,
        self_critic_regenerated=self_critic_regenerated,
        trace_id=ctx.trace_id,
    )
    ctx.extra["generated_decision"] = decision

    return PhaseResult(
        answer=answer,
        citations=citations,
        followup=followup,
        confidence=confidence,
        answer_plan=answer_plan,
        generated_decision=decision,
    )
