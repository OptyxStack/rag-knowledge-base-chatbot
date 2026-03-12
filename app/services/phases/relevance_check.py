"""Conversation history relevance check. LLM verifies if history is relevant to current query before generate."""

import json

from app.core.config import get_settings
from app.core.logging import get_logger
from app.services.conversation_context import truncate_for_prompt
from app.services.flow_debug import _pipeline_log
from app.services.model_router import get_model_for_task
from app.services.schemas import RelevanceCheckResult

logger = get_logger(__name__)

_RELEVANCE_CHECK_PROMPT = """You are a relevance checker for a support RAG system.

Given:
1. Current user question: "{effective_query}"
2. Conversation history (most recent last):
{history_block}

Determine: Is the conversation history RELEVANT to answering the current question?

Relevant = the history provides context that the current question BUILDS ON or REFERS TO.
Examples of RELEVANT:
- User asked "refund for VPS?" then "what about proxies?" → same topic (refund)
- User asked "pricing for Plan A" then "and Plan B?" → comparison, same intent
- User said "I have Windows" then "which plan fits?" → history clarifies constraints

Examples of NOT RELEVANT:
- User asked about refund, now asks "what services do you offer?" → new topic
- User asked about pricing, now asks "how to cancel?" → different intent
- History is greeting/small talk, current question is substantive → no real context

Return JSON only, no markdown:
{{
  "relevant": true | false,
  "reason": "one short sentence",
  "relevant_turn_count": 0 | 1 | 2 | "all"
}}

- relevant_turn_count: how many recent turns (from the end) are still relevant. Use 0 when relevant=false. Use "all" when the entire history is relevant.
"""


def _format_history_for_check(history: list[dict[str, str]], max_turns: int) -> str:
    """Format conversation history for the prompt. Each turn = user + assistant pair."""
    if not history:
        return "(empty)"
    # Take last N turns (user+assistant pairs)
    turns = history[-(max_turns * 2) :] if len(history) > max_turns * 2 else history
    lines = []
    for i, msg in enumerate(turns):
        role = msg.get("role", "unknown")
        content = (msg.get("content") or "")[:300]
        if len((msg.get("content") or "")) > 300:
            content += "..."
        lines.append(f"  [{role}]: {content}")
    return "\n".join(lines) if lines else "(empty)"


def _parse_relevance_response(content: str) -> RelevanceCheckResult | None:
    """Parse LLM JSON response."""
    content = (content or "").strip()
    if not content:
        return None
    if "```json" in content:
        start = content.find("```json")
        end = content.rfind("```")
        if start != -1 and end != -1 and end > start:
            content = content[start + 7 : end].strip()
    elif "```" in content:
        start = content.find("```")
        end = content.rfind("```")
        if start != -1 and end != -1 and end > start:
            content = content[start + 3 : end].strip()
    try:
        data = json.loads(content)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    relevant = bool(data.get("relevant", True))
    reason = str(data.get("reason", "")).strip()
    relevant_turn_count = data.get("relevant_turn_count", "all")
    if isinstance(relevant_turn_count, int) and relevant_turn_count >= 0:
        pass
    else:
        relevant_turn_count = "all" if relevant else 0
    return RelevanceCheckResult(
        relevant=relevant,
        reason=reason,
        relevant_turn_count=relevant_turn_count,
    )


async def execute_relevance_check(
    effective_query: str,
    conversation_history: list[dict[str, str]],
    *,
    llm,
    trace_id: str | None = None,
) -> RelevanceCheckResult | None:
    """
    Check if conversation history is relevant to the current query.
    Returns RelevanceCheckResult or None on failure (fallback: use full history).
    """
    settings = get_settings()
    if not getattr(settings, "conversation_relevance_check_enabled", True):
        return None
    if not conversation_history:
        return None

    truncated = truncate_for_prompt(conversation_history)
    if not truncated:
        return None

    max_turns = getattr(settings, "conversation_relevance_check_max_history_turns", 5) or 5
    history_block = _format_history_for_check(truncated, max_turns)

    model = getattr(settings, "conversation_relevance_check_model", None) or ""
    if not model:
        model = get_model_for_task("conversation_relevance_check")

    prompt = _RELEVANCE_CHECK_PROMPT.format(
        effective_query=effective_query[:500],
        history_block=history_block,
    )

    _pipeline_log(
        "relevance_check",
        "start",
        history_turns=len(truncated) // 2,
        trace_id=trace_id,
    )

    try:
        from app.core.tracing import current_llm_task_var

        current_llm_task_var.set("conversation_relevance_check")
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": "Return only valid JSON. No markdown."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.0,
            model=model,
            max_tokens=150,
        )
        content = getattr(resp, "content", "") or ""
        result = _parse_relevance_response(content)
        if result:
            _pipeline_log(
                "relevance_check",
                "done",
                relevant=result.relevant,
                reason=result.reason[:80] if result.reason else "",
                trace_id=trace_id,
            )
            return result
    except Exception as e:
        logger.warning("relevance_check_failed", error=str(e), trace_id=trace_id)
        _pipeline_log("relevance_check", "failed", error=str(e), trace_id=trace_id)

    return None
