"""Answer generation utilities: formatting, parsing, retrieval query resolution."""

import json
import re
from typing import Any

from app.core.logging import get_logger
from app.search.base import EvidenceChunk
from app.services.evidence_quality import QualityReport
from app.services.schemas import AnswerPlan, DecisionResult, QuerySpec

logger = get_logger(__name__)


def format_evidence_for_prompt(evidence: list[EvidenceChunk], max_chars_per_chunk: int = 1200) -> str:
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


def parse_llm_response(content: str) -> dict[str, Any]:
    """Parse LLM JSON response, with fallback."""
    content = content.strip()
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


def build_answer_plan(
    decision_router: DecisionResult | None,
    query_spec: QuerySpec | None,
    quality_report: QualityReport | None,
) -> AnswerPlan:
    """Build a minimal lane-aware answer plan for the current generation pass."""
    # Routine question (skip_retrieval): no evidence, greeting plan
    if query_spec and getattr(query_spec, "skip_retrieval", False):
        return AnswerPlan(
            lane="PASS_STRONG",
            allowed_claim_scope="full",
            must_include=[
                "Respond with a friendly, concise greeting. No evidence provided - this is a social message.",
            ],
            must_avoid=[],
            required_citations=[],
            output_blocks=["direct_answer"],
            tone_policy="friendly",
            generation_constraints={"confidence_cap": 1.0},
        )

    lane = decision_router.resolved_lane() if decision_router else "PASS_STRONG"
    if lane not in ("PASS_STRONG", "PASS_WEAK"):
        lane = "PASS_STRONG"

    if lane == "PASS_WEAK":
        missing_signals = quality_report.missing_signals[:3] if quality_report else []
        must_include = [
            "Answer only with details explicitly supported by the evidence.",
            "State clearly which requested details remain unconfirmed.",
            "Provide a short next step for the missing details.",
        ]
        if missing_signals:
            must_include.append(
                f"Keep the missing areas explicit: {', '.join(missing_signals)}."
            )
        return AnswerPlan(
            lane="PASS_WEAK",
            allowed_claim_scope="partial",
            must_include=must_include,
            must_avoid=[
                "Do not invent missing pricing, links, policy clauses, or steps.",
                "Do not present assumptions as confirmed facts.",
            ],
            required_citations=list(
                dict.fromkeys(
                    (query_spec.hard_requirements or [])
                    if query_spec and getattr(query_spec, "hard_requirements", None)
                    else []
                )
            ),
            output_blocks=[
                "direct_answer",
                "uncertain_points",
                "recommended_next_step",
                "citations",
            ],
            tone_policy="cautious",
            generation_constraints={
                "confidence_cap": 0.6,
                "bounded_suffix": (
                    "I only confirmed the details above from the available evidence. "
                    "Some requested specifics are still unverified."
                ),
            },
        )

    return AnswerPlan(
        lane="PASS_STRONG",
        allowed_claim_scope="full",
        must_include=[
            "Answer directly using only the provided evidence.",
            "Cite each key claim with the provided chunks only.",
        ],
        must_avoid=[
            "Do not add facts that are not in the evidence.",
        ],
        required_citations=list(
            dict.fromkeys(
                query_spec.required_evidence
                if query_spec and getattr(query_spec, "required_evidence", None)
                else []
            )
        ),
        output_blocks=["direct_answer", "citations"],
        tone_policy="concise",
        generation_constraints={"confidence_cap": 0.9},
    )


def format_answer_plan_instruction(
    answer_plan: AnswerPlan,
    quality_report: QualityReport | None,
) -> str:
    """Convert an AnswerPlan into a prompt-safe instruction block."""
    if answer_plan.lane == "PASS_WEAK":
        lines = [
            "ROUTING DECISION: PASS_WEAK.",
            "The router has already determined that a bounded partial answer is allowed.",
            "Keep decision as PASS unless the evidence is contradictory or unusable.",
            "Answer only with facts explicitly supported by the evidence.",
            "Explicitly say which requested details are not confirmed in the evidence.",
            "Keep the answer concise and cautious.",
        ]
        if quality_report and quality_report.missing_signals:
            lines.append(
                "Known missing signals: "
                + ", ".join(quality_report.missing_signals[:3])
                + "."
            )
        return "\n".join(lines)

    return "\n".join(
        [
            "ROUTING DECISION: PASS_STRONG.",
            "The router has determined the evidence is sufficient for a direct grounded answer.",
            "Answer directly using only the provided evidence and keep decision as PASS.",
        ]
    )


def apply_answer_plan(
    answer_plan: AnswerPlan,
    parsed: dict[str, Any],
) -> tuple[str, str, list[str], float]:
    """Apply lane constraints after parsing the LLM response."""
    decision = str(parsed.get("decision", "ASK_USER")).upper()
    answer = str(parsed.get("answer", ""))
    raw_followup = parsed.get("followup_questions", [])
    followup = (
        [str(item) for item in raw_followup if isinstance(item, str)]
        if isinstance(raw_followup, list)
        else []
    )

    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0

    constraints = answer_plan.generation_constraints or {}
    confidence_cap = constraints.get("confidence_cap")
    if isinstance(confidence_cap, (int, float)):
        confidence = min(confidence, float(confidence_cap))

    if answer_plan.lane == "PASS_WEAK" and answer.strip():
        if decision != "ESCALATE":
            decision = "PASS"
        followup = []

        bounded_suffix = str(constraints.get("bounded_suffix", "")).strip()
        lower_answer = answer.lower()
        bounded_markers = (
            "not confirmed",
            "unverified",
            "not available in the evidence",
            "not provided in the evidence",
            "could not verify",
            "i only confirmed",
        )
        if (
            bounded_suffix
            and bounded_suffix.lower() not in lower_answer
            and not any(marker in lower_answer for marker in bounded_markers)
        ):
            answer = f"{answer.rstrip()}\n\n{bounded_suffix}"

    return decision, answer, followup, confidence


def collect_rewrite_candidates(
    base_query: str,
    query_spec: QuerySpec | None,
) -> list[str]:
    """Collect deduplicated retrieval rewrite candidates for retries."""
    candidates = [base_query.strip()]
    if query_spec and getattr(query_spec, "rewrite_candidates", None):
        candidates.extend(
            str(candidate).strip()
            for candidate in query_spec.rewrite_candidates
            if isinstance(candidate, str) and candidate.strip()
        )

    deduped: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        key = candidate.lower()
        if key not in seen:
            seen.add(key)
            deduped.append(candidate)
    return deduped


def _pick_intent_aligned_rewrite(
    base_query: str,
    rewrite_candidates: list[str],
    attempt: int,
    retry_strategy: Any | None,
    query_spec: QuerySpec | None,
) -> tuple[str | None, str]:
    """Pick rewrite that aligns with retry intent to avoid semantic drift.

    E.g. for missing_policy + filter_doc_types [policy,tos], prefer
    'refund policy' over 'order cancellation' (which can match order links).
    """
    if attempt <= 1 or not retry_strategy or not rewrite_candidates:
        return None, ""

    filter_doc_types = getattr(retry_strategy, "filter_doc_types", None) or []
    hard = set(getattr(query_spec, "hard_requirements", None) or []) if query_spec else set()
    reqs = set(getattr(query_spec, "required_evidence", None) or []) if query_spec else set()
    needs_policy = "policy_language" in hard or "policy_language" in reqs
    base_lower = base_query.lower().strip()

    # Policy/tos retry: prefer policy-focused phrases
    if set(filter_doc_types) & {"policy", "tos"} or needs_policy:
        policy_terms = ("policy", "terms", "refund", "cancellation policy", "terms of service")
        drift_terms = ("order", "buy", "checkout")  # can match transaction pages
        for c in rewrite_candidates[1:]:  # skip base
            c_lower = c.lower().strip()
            if not c_lower or c_lower == base_lower:
                continue
            if any(p in c_lower for p in policy_terms) and not any(d in c_lower for d in drift_terms):
                return c.strip(), "intent_aligned_rewrite"
    # Steps/howto retry: prefer step-focused phrases
    boost = getattr(retry_strategy, "boost_patterns", None) or []
    if any(b in ("step", "1.", "2.", "first", "second") for b in boost):
        step_terms = ("step", "how to", "guide", "setup", "install")
        for c in rewrite_candidates[1:]:
            c_lower = c.lower().strip()
            if c_lower and c_lower != base_lower and any(s in c_lower for s in step_terms):
                return c.strip(), "intent_aligned_rewrite"

    return None, ""


def resolve_retrieval_query(
    *,
    base_query: str,
    attempt: int,
    query_spec: QuerySpec | None,
    retry_strategy: Any | None,
    explicit_override: str | None = None,
) -> tuple[str, str, list[str]]:
    """Resolve the retrieval query for the current attempt.

    This keeps the user-facing effective query stable while allowing retrieval
    retries to use structured rewrite candidates or explicit overrides.
    Intent-aligned selection avoids semantic drift (e.g. 'order cancellation'
    matching order links instead of policy).
    """
    rewrite_candidates = collect_rewrite_candidates(base_query, query_spec)

    if retry_strategy and getattr(retry_strategy, "suggested_query", None):
        suggested = str(retry_strategy.suggested_query).strip()
        if suggested:
            return suggested, "retry_strategy_suggested_query", rewrite_candidates

    if explicit_override and explicit_override.strip():
        return explicit_override.strip(), "explicit_retry_query", rewrite_candidates

    if attempt > 1 and len(rewrite_candidates) > 1:
        # Prefer intent-aligned rewrite to avoid semantic drift
        picked, reason = _pick_intent_aligned_rewrite(
            base_query, rewrite_candidates, attempt, retry_strategy, query_spec
        )
        if picked:
            return picked, reason, rewrite_candidates

        idx = min(attempt - 1, len(rewrite_candidates) - 1)
        candidate = rewrite_candidates[idx].strip()
        if candidate:
            return candidate, f"rewrite_candidate_{idx}", rewrite_candidates

    return base_query.strip(), "base_query", rewrite_candidates
