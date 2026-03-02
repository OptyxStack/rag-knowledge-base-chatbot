"""Answer generation with grounding and reviewer gate."""

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk
from app.services.branding_config import get_system_prompt, match_intent
from app.services.evidence_hygiene import compute_hygiene
from app.services.decision_router import route as decision_route, route_hybrid as decision_route_hybrid
from app.services.evidence_quality import (
    evaluate_quality,
    infer_required_evidence,
    passes_quality_gate,
    QualityReport,
)
from app.services.llm_config import get_llm_fallback_model, get_llm_model
from app.services.llm_gateway import LLMGateway, get_llm_gateway
from app.services.orchestrator import Orchestrator
from app.services.archi_config import (
    get_decision_router_use_llm,
    get_evidence_evaluator_enabled,
    get_final_polish_enabled,
    get_language_detect_enabled,
    get_self_critic_enabled,
)
from app.services.language_detect import detect_language
from app.services.normalizer import normalize as normalize_query
from app.services.retrieval import EvidencePack, RetrievalService
from app.services.evidence_evaluator import evaluate_evidence
from app.services.retry_planner import plan_retry
from app.services.self_critic import SelfCriticResult, critique as self_critic
from app.services.final_polish import polish as final_polish
from app.services.schemas import DecisionResult, QuerySpec
from app.services.reviewer import ReviewerGate, ReviewerResult, ReviewerStatus

logger = get_logger(__name__)


@dataclass
class AnswerOutput:
    """Structured answer output."""

    decision: str  # PASS | ASK_USER | ESCALATE
    answer: str
    followup_questions: list[str]
    citations: list[dict[str, str]]
    confidence: float
    debug: dict[str, Any] = field(default_factory=dict)


def _build_flow_debug(
    *,
    trace_id: str | None,
    evidence_pack: EvidencePack | None,
    evidence: list[EvidenceChunk],
    messages: list[dict[str, str]],
    model_used: str,
    llm_tokens: dict[str, int] | None = None,
    attempt: int = 1,
    reviewer_reasons: list[str] | None = None,
    max_attempts_reached: bool = False,
    finish_reason: str | None = None,
    quality_report: QualityReport | None = None,
    retry_strategy_applied: dict[str, Any] | None = None,
    query_spec: QuerySpec | None = None,
    decision_router: DecisionResult | None = None,
    source_lang: str | None = None,
    evidence_eval_result: dict[str, Any] | None = None,
    self_critic_regenerated: bool = False,
    final_polish_applied: bool = False,
) -> dict[str, Any]:
    """Build debug dict for flow inspection (internal admin)."""
    debug: dict[str, Any] = {
        "trace_id": trace_id,
        "attempt": attempt,
        "model_used": model_used,
    }
    if evidence_pack:
        debug["retrieval_stats"] = evidence_pack.retrieval_stats
        qr = evidence_pack.retrieval_stats.get("query_rewrite")
        if qr:
            debug["query_rewrite"] = qr
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
        }
    if decision_router:
        debug["decision_router"] = {
            "decision": decision_router.decision,
            "reason": decision_router.reason,
        }
    if source_lang:
        debug["source_lang"] = source_lang
    if evidence_eval_result:
        debug["evidence_eval"] = evidence_eval_result
    if self_critic_regenerated:
        debug["self_critic_regenerated"] = True
    if final_polish_applied:
        debug["final_polish_applied"] = True
    return debug


def _format_evidence_for_prompt(evidence: list[EvidenceChunk], max_chars_per_chunk: int = 1200) -> str:
    """Format evidence for LLM prompt. Truncates each chunk to stay within context limits."""
    parts = []
    for i, e in enumerate(evidence, 1):
        text = (e.full_text or e.snippet) or ""
        if len(text) > max_chars_per_chunk:
            text = text[:max_chars_per_chunk] + "..."
        parts.append(
            f"[Chunk {e.chunk_id}]\n"
            f"Source: {e.source_url}\n"
            f"Type: {e.doc_type}\n"
            f"Content: {text}\n"
        )
    return "\n---\n".join(parts)


def _parse_llm_response(content: str) -> dict[str, Any]:
    """Parse LLM JSON response, with fallback."""
    # Try to extract JSON from response
    content = content.strip()
    # Handle markdown code blocks
    if "```json" in content:
        match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
        if match:
            content = match.group(1)
    elif "```" in content:
        match = re.search(r"```\s*([\s\S]*?)\s*```", content)
        if match:
            content = match.group(1)

    try:
        return json.loads(content)
    except json.JSONDecodeError as e:
        logger.warning("llm_json_parse_failed", error=str(e), content_preview=content[:200])
        return {
            "decision": "ASK_USER",
            "answer": content[:500] if content else "I couldn't format my response properly. Could you rephrase your question?",
            "followup_questions": ["Could you provide more details about your question?"],
            "citations": [],
            "confidence": 0.0,
        }


class AnswerService:
    """Orchestrates retrieval, LLM generation, and reviewer gate."""

    def __init__(
        self,
        retrieval: RetrievalService | None = None,
        llm: LLMGateway | None = None,
        reviewer: ReviewerGate | None = None,
        orchestrator: Orchestrator | None = None,
    ) -> None:
        self._settings = get_settings()
        self._retrieval = retrieval or RetrievalService()
        self._llm = llm or get_llm_gateway()
        self._reviewer = reviewer or ReviewerGate()
        self._orchestrator = orchestrator or Orchestrator(
            primary_model=get_llm_model(),
            fallback_model=get_llm_fallback_model(),
        )

    async def generate(
        self,
        query: str,
        conversation_history: list[dict[str, str]] | None = None,
        trace_id: str | None = None,
    ) -> AnswerOutput:
        """Generate grounded answer with retrieval and reviewer gate."""
        # Intent cache: common queries (who am i, what can you do) - no LLM call
        intent = match_intent(query)
        if intent:
            logger.debug("intent_cache_hit", intent=intent.intent)
            return AnswerOutput(
                decision="PASS",
                answer=intent.answer,
                followup_questions=[],
                citations=[],
                confidence=1.0,
                debug={
                    "trace_id": trace_id,
                    "intent_cache": intent.intent,
                },
            )

        # Language detection (archi_v3, non-LLM)
        source_lang = detect_language(query) if get_language_detect_enabled() else "en"

        # Phase 2: Normalizer (QuerySpec, language-aware when use_llm)
        query_spec: QuerySpec | None = None
        if getattr(self._settings, "normalizer_enabled", True):
            query_spec = await normalize_query(query, conversation_history, source_lang=source_lang)

        # Effective query for retrieval/LLM: use canonical English when translated
        effective_query = query
        if query_spec and query_spec.canonical_query_en:
            effective_query = query_spec.canonical_query_en

        # Pre-retrieval gate: greetings/social need NO retrieval
        if query_spec and query_spec.skip_retrieval and query_spec.canned_response:
            return AnswerOutput(
                decision="PASS",
                answer=query_spec.canned_response,
                followup_questions=[],
                citations=[],
                confidence=1.0,
                debug={
                    "trace_id": trace_id,
                    "skip_retrieval": True,
                    "intent": query_spec.intent,
                    "source_lang": source_lang,
                },
            )

        # Phase 3: Ambiguous short-circuit (no retrieval, no LLM)
        if (
            query_spec
            and query_spec.is_ambiguous
            and getattr(self._settings, "decision_router_enabled", True)
        ):
            dr = decision_route(query_spec, None, [], [], True)
            return AnswerOutput(
                decision=dr.decision,
                answer=dr.answer,
                followup_questions=dr.clarifying_questions,
                citations=[],
                confidence=0.0,
                debug=_build_flow_debug(
                    trace_id=trace_id,
                    evidence_pack=None,
                    evidence=[],
                    messages=[],
                    model_used=self._orchestrator.get_model_for_query(query),
                    query_spec=query_spec,
                    decision_router=dr,
                    source_lang=source_lang,
                ),
            )

        required_evidence = (
            query_spec.required_evidence if query_spec else infer_required_evidence(query)
        )

        max_attempts = self._settings.max_retrieval_attempts
        attempt = 1
        evidence_pack: EvidencePack | None = None
        last_reviewer_result: ReviewerResult | None = None
        quality_report: QualityReport | None = None
        retry_strategy_applied: dict[str, Any] | None = None
        evidence_eval_result = None
        retrieval_latency_ms = 0.0
        latency_budget_ms = getattr(self._settings, "retrieval_latency_budget_ms", 0)
        self_critic_regenerated = False
        model = self._orchestrator.get_model_for_query(query)
        messages: list[dict[str, str]] = []
        llm_resp = None
        answer = ""
        followup: list[str] = []
        citations: list[str] = []
        confidence = 0.0

        while attempt <= max_attempts:
            # Attempt 1: broad hybrid. Attempt 2: precision from Retry Planner (+ Evidence Evaluator)
            retry_strategy = (
                plan_retry(
                    quality_report.missing_signals if quality_report else [],
                    2,
                    evidence_eval_result=evidence_eval_result,
                )
                if attempt == 2
                else None
            )
            if retry_strategy:
                retry_strategy_applied = {
                    "boost_patterns": retry_strategy.boost_patterns[:5],
                    "filter_doc_types": retry_strategy.filter_doc_types,
                    "context_expansion": retry_strategy.context_expansion,
                    "suggested_query": retry_strategy.suggested_query,
                }

            t0 = time.monotonic()
            evidence_pack = await self._retrieval.retrieve(
                effective_query,
                conversation_history=conversation_history,
                retry_strategy=retry_strategy,
                attempt=attempt,
                query_spec=query_spec,
            )
            retrieval_latency_ms += (time.monotonic() - t0) * 1000
            evidence = evidence_pack.chunks

            # Budget: stop retry if latency exceeded
            if latency_budget_ms > 0 and retrieval_latency_ms > latency_budget_ms:
                logger.warning("retrieval_latency_budget_exceeded", ms=retrieval_latency_ms, budget=latency_budget_ms)
                break

            if not evidence:
                return AnswerOutput(
                    decision="ASK_USER",
                    answer="I couldn't find relevant information in our knowledge base. Could you rephrase your question or provide more context?",
                    followup_questions=["What specific topic are you asking about?"],
                    citations=[],
                    confidence=0.0,
                    debug=_build_flow_debug(
                        trace_id=trace_id,
                        evidence_pack=evidence_pack,
                        evidence=[],
                        messages=[],
                        model_used=self._orchestrator.get_model_for_query(query),
                        attempt=attempt,
                        quality_report=quality_report,
                        retry_strategy_applied=retry_strategy_applied,
                        query_spec=query_spec,
                        source_lang=source_lang,
                    ),
                )

            # LLM Evidence Evaluator (archi_v3) – advises Retry Planner for attempt 2
            if get_evidence_evaluator_enabled():
                evidence_eval_result = await evaluate_evidence(
                    effective_query,
                    query_spec,
                    evidence,
                    top_n=5,
                )

            # Evidence Hygiene (Phase 0.5): log only, no gating
            hygiene = compute_hygiene(evidence)
            if evidence_pack.retrieval_stats:
                evidence_pack.retrieval_stats["evidence_signatures"] = {
                    "pct_chunks_with_url": round(hygiene.pct_chunks_with_url, 1),
                    "pct_chunks_with_number_unit": round(hygiene.pct_chunks_with_number_unit, 1),
                    "pct_chunks_boilerplate_gt_06": round(hygiene.pct_chunks_boilerplate_gt_06, 1),
                    "median_content_density": round(hygiene.median_content_density, 3),
                }

            # Evidence Quality Gate (Phase 1): retry if required features not met
            quality_report = evaluate_quality(evidence, required_evidence)
            try:
                from app.core.metrics import evidence_quality_score
                evidence_quality_score.observe(quality_report.quality_score)
            except Exception:
                pass
            if not passes_quality_gate(quality_report, required_evidence) and attempt < max_attempts:
                attempt += 1
                continue

            # Phase 3: Decision Router (before LLM) – hybrid when use_llm
            if getattr(self._settings, "decision_router_enabled", True):
                if get_decision_router_use_llm():
                    dr = await decision_route_hybrid(
                        query_spec,
                        quality_report,
                        evidence,
                        required_evidence,
                        passes_quality_gate(quality_report, required_evidence),
                        query=effective_query,
                    )
                else:
                    dr = decision_route(
                        query_spec,
                        quality_report,
                        evidence,
                        required_evidence,
                        passes_quality_gate(quality_report, required_evidence),
                    )
                if dr.decision != "PASS":
                    try:
                        from app.core.metrics import decision_total
                        decision_total.labels(decision=dr.decision).inc()
                    except Exception:
                        pass
                    if dr.decision == "ESCALATE":
                        try:
                            from app.core.metrics import escalation_rate
                            escalation_rate.inc()
                        except Exception:
                            pass
                    evidence_eval_debug = (
                        {
                            "relevance_score": evidence_eval_result.relevance_score,
                            "retry_needed": evidence_eval_result.retry_needed,
                            "coverage_gaps": evidence_eval_result.coverage_gaps[:3],
                        }
                        if evidence_eval_result
                        else None
                    )
                    return AnswerOutput(
                        decision=dr.decision,
                        answer=dr.answer,
                        followup_questions=dr.clarifying_questions,
                        citations=[],
                        confidence=0.0,
                        debug=_build_flow_debug(
                            trace_id=trace_id,
                            evidence_pack=evidence_pack,
                            evidence=evidence,
                            messages=[],
                            model_used=self._orchestrator.get_model_for_query(query),
                            attempt=attempt,
                            quality_report=quality_report,
                            retry_strategy_applied=retry_strategy_applied,
                            query_spec=query_spec,
                            decision_router=dr,
                            source_lang=source_lang,
                            evidence_eval_result=evidence_eval_debug,
                        ),
                    )

            # Build messages
            max_chars = self._settings.llm_max_evidence_chars
            evidence_block = _format_evidence_for_prompt(evidence, max_chars)
            user_content = f"User question: {effective_query}\n\nEvidence:\n{evidence_block}"
            messages = [{"role": "system", "content": get_system_prompt()}]
            if conversation_history:
                for m in conversation_history[-4:]:  # Last 4 messages (fit 16k context)
                    messages.append({"role": m["role"], "content": m["content"]})
            messages.append({"role": "user", "content": user_content})

            # LLM call (model routing via orchestrator)
            model = self._orchestrator.get_model_for_query(query)
            try:
                llm_resp = await self._llm.chat(
                    messages=messages,
                    temperature=self._settings.llm_temperature,
                    model=model,
                )
            except Exception as e:
                logger.error("answer_llm_failed", error=str(e))
                return AnswerOutput(
                    decision="ESCALATE",
                    answer="I'm sorry, I encountered an error. Please try again or contact support.",
                    followup_questions=[],
                    citations=[],
                    confidence=0.0,
                        debug={
                            **_build_flow_debug(
                                trace_id=trace_id,
                                evidence_pack=evidence_pack,
                                evidence=evidence,
                                messages=messages,
                                model_used=model,
                                quality_report=quality_report,
                                retry_strategy_applied=retry_strategy_applied,
                                query_spec=query_spec,
                                source_lang=source_lang,
                            ),
                            "error": str(e),
                        },
                )

            # Detect truncation (model hit max_tokens)
            if getattr(llm_resp, "finish_reason", None) == "length":
                logger.warning(
                    "llm_response_truncated",
                    trace_id=trace_id,
                    output_tokens=getattr(llm_resp, "output_tokens", 0),
                )

            parsed = _parse_llm_response(llm_resp.content)
            decision = parsed.get("decision", "ASK_USER")
            answer = parsed.get("answer", "")
            followup = parsed.get("followup_questions", [])
            citations = parsed.get("citations", [])
            confidence = float(parsed.get("confidence", 0.0))

            # Self-Critic (archi_v3): regenerate on fail, max 1
            gen_attempt = 1
            self_critic_regenerated = False
            max_gen_attempts = 1 + getattr(self._settings, "self_critic_regenerate_max", 1)
            while gen_attempt <= max_gen_attempts:
                if get_self_critic_enabled() and gen_attempt < max_gen_attempts:
                    critique_result = await self_critic(effective_query, answer, citations, evidence)
                    if critique_result and not critique_result.pass_:
                        try:
                            from app.core.metrics import self_critic_regenerate_total
                            self_critic_regenerate_total.inc()
                        except Exception:
                            pass
                        logger.info(
                            "self_critic_fail",
                            issues=critique_result.issues[:3],
                            suggested_fix_preview=critique_result.suggested_fix[:150] if critique_result.suggested_fix else None,
                            regenerating=True,
                        )
                        # Regenerate with feedback
                        feedback = f"\n\nPrevious attempt had issues: {', '.join(critique_result.issues[:2])}. Fix: {critique_result.suggested_fix}"
                        messages[-1]["content"] = messages[-1]["content"] + feedback
                        try:
                            llm_resp = await self._llm.chat(
                                messages=messages,
                                temperature=self._settings.llm_temperature,
                                model=model,
                            )
                            parsed = _parse_llm_response(llm_resp.content)
                            decision = parsed.get("decision", "ASK_USER")
                            answer = parsed.get("answer", "")
                            followup = parsed.get("followup_questions", [])
                            citations = parsed.get("citations", [])
                            confidence = float(parsed.get("confidence", 0.0))
                        except Exception as e:
                            logger.warning("self_critic_regenerate_failed", error=str(e))
                        self_critic_regenerated = True
                        gen_attempt += 1
                        continue
                break

            # Reviewer gate
            last_reviewer_result = self._reviewer.review(
                decision=decision,
                answer=answer,
                citations=citations,
                evidence=evidence,
                query=query,
                confidence=confidence,
                retrieval_attempt=attempt,
                max_attempts=max_attempts,
            )

            if last_reviewer_result.status == ReviewerStatus.PASS:
                try:
                    from app.core.metrics import decision_total
                    decision_total.labels(decision="PASS").inc()
                except Exception:
                    pass
                # Final Polish (archi_v3)
                final_polish_applied = False
                if get_final_polish_enabled():
                    polished = await final_polish(answer)
                    if polished:
                        answer = polished
                        final_polish_applied = True
                evidence_eval_debug = (
                    {
                        "relevance_score": evidence_eval_result.relevance_score,
                        "retry_needed": evidence_eval_result.retry_needed,
                        "coverage_gaps": evidence_eval_result.coverage_gaps[:3],
                    }
                    if evidence_eval_result
                    else None
                )
                return AnswerOutput(
                    decision="PASS",
                    answer=answer,
                    followup_questions=[],
                    citations=citations,
                    confidence=confidence,
                    debug=_build_flow_debug(
                        trace_id=trace_id,
                        evidence_pack=evidence_pack,
                        evidence=evidence,
                        messages=messages,
                        model_used=model,
                        llm_tokens={"input": llm_resp.input_tokens, "output": llm_resp.output_tokens},
                        attempt=attempt,
                        finish_reason=getattr(llm_resp, "finish_reason", None),
                        quality_report=quality_report,
                        retry_strategy_applied=retry_strategy_applied,
                        query_spec=query_spec,
                        source_lang=source_lang,
                        evidence_eval_result=evidence_eval_debug,
                        self_critic_regenerated=self_critic_regenerated,
                        final_polish_applied=final_polish_applied,
                    ),
                )

            if last_reviewer_result.status == ReviewerStatus.ASK_USER:
                try:
                    from app.core.metrics import decision_total
                    decision_total.labels(decision="ASK_USER").inc()
                except Exception:
                    pass
                return AnswerOutput(
                    decision="ASK_USER",
                    answer=answer,
                    followup_questions=followup or ["Could you provide more details?"],
                    citations=citations,
                    confidence=confidence,
                    debug=_build_flow_debug(
                        trace_id=trace_id,
                        evidence_pack=evidence_pack,
                        evidence=evidence,
                        messages=messages,
                        model_used=model,
                        llm_tokens={"input": llm_resp.input_tokens, "output": llm_resp.output_tokens},
                        attempt=attempt,
                        reviewer_reasons=last_reviewer_result.reasons,
                        finish_reason=getattr(llm_resp, "finish_reason", None),
                        quality_report=quality_report,
                        retry_strategy_applied=retry_strategy_applied,
                        query_spec=query_spec,
                        source_lang=source_lang,
                        evidence_eval_result=(
                            {
                                "relevance_score": evidence_eval_result.relevance_score,
                                "retry_needed": evidence_eval_result.retry_needed,
                            }
                            if evidence_eval_result
                            else None
                        ),
                        self_critic_regenerated=self_critic_regenerated,
                    ),
                )

            if last_reviewer_result.status == ReviewerStatus.ESCALATE:
                try:
                    from app.core.metrics import decision_total, escalation_rate
                    decision_total.labels(decision="ESCALATE").inc()
                    escalation_rate.inc()
                except Exception:
                    pass
                return AnswerOutput(
                    decision="ESCALATE",
                    answer=answer or "This request requires human review. A support agent will follow up.",
                    followup_questions=[],
                    citations=citations,
                    confidence=confidence,
                    debug=_build_flow_debug(
                        trace_id=trace_id,
                        evidence_pack=evidence_pack,
                        evidence=evidence,
                        messages=messages,
                        model_used=model,
                        llm_tokens={"input": llm_resp.input_tokens, "output": llm_resp.output_tokens},
                        attempt=attempt,
                        reviewer_reasons=last_reviewer_result.reasons,
                        finish_reason=getattr(llm_resp, "finish_reason", None),
                        quality_report=quality_report,
                        retry_strategy_applied=retry_strategy_applied,
                        query_spec=query_spec,
                        source_lang=source_lang,
                        evidence_eval_result=(
                            {
                                "relevance_score": evidence_eval_result.relevance_score,
                                "retry_needed": evidence_eval_result.retry_needed,
                            }
                            if evidence_eval_result
                            else None
                        ),
                        self_critic_regenerated=self_critic_regenerated,
                    ),
                )

            # RETRIEVE_MORE - prefer Retry Planner (missing_signals) over LLM suggested_queries
            if last_reviewer_result.suggested_queries and attempt < max_attempts:
                if quality_report and quality_report.missing_signals:
                    attempt += 1
                    continue  # Loop will use retry_strategy from plan_retry(missing_signals, 2)
                query = last_reviewer_result.suggested_queries[0]
                attempt += 1
                continue

            attempt += 1

        # Max attempts reached (evidence, messages, model, llm_resp from last iteration)
        return AnswerOutput(
            decision="ASK_USER",
            answer=answer if last_reviewer_result else "I need more information to help. Could you clarify your question?",
            followup_questions=followup or ["What specifically would you like to know?"],
            citations=citations,
            confidence=confidence,
                debug=_build_flow_debug(
                    trace_id=trace_id,
                    evidence_pack=evidence_pack,
                    evidence=evidence if evidence_pack else [],
                    messages=messages if evidence_pack else [],
                    model_used=model if evidence_pack else self._orchestrator.get_model_for_query(query),
                    llm_tokens={"input": llm_resp.input_tokens, "output": llm_resp.output_tokens} if evidence_pack and llm_resp else None,
                    attempt=attempt,
                    reviewer_reasons=last_reviewer_result.reasons if last_reviewer_result else None,
                    max_attempts_reached=True,
                    finish_reason=getattr(llm_resp, "finish_reason", None) if evidence_pack and llm_resp else None,
                    quality_report=quality_report,
                    retry_strategy_applied=retry_strategy_applied,
                    query_spec=query_spec,
                    source_lang=source_lang,
                    evidence_eval_result=(
                        {
                            "relevance_score": evidence_eval_result.relevance_score,
                            "retry_needed": evidence_eval_result.retry_needed,
                        }
                        if evidence_eval_result
                        else None
                    ),
                    self_critic_regenerated=self_critic_regenerated,
                ),
        )
