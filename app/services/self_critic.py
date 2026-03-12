"""LLM self-critic: checks answer grounding and completeness."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk
from app.services.llm_gateway import get_llm_gateway
from app.services.model_router import get_model_for_task

logger = get_logger(__name__)

SELF_CRITIC_PROMPT = """You are a quality reviewer for a support chatbot answer.

Check whether the answer is grounded in evidence and complete enough for the query context.
Output JSON only:
{
  "pass": true,
  "issues": [],
  "suggested_fix": ""
}

Set pass=false if any of these are true:
- Unsupported claims or hallucinations
- Missing critical evidence-backed facts
- Overgeneralization that contradicts evidence scope
- Incomplete option coverage: the query/evidence expects multiple actionable options/paths but answer omits major ones

Completeness check (when require_completeness in context is true): Verify the answer covers all major options from evidence. If evidence has multiple distinct methods/plans/paths (e.g. different access methods, different plans, different steps) and the answer mentions only one or omits key alternatives, set pass=false with issue "Incomplete: answer omits [X] from evidence".

issues: concise, specific problems
suggested_fix: one short instruction for regeneration"""


@dataclass
class SelfCriticResult:
    """Self-critic output."""

    pass_: bool
    issues: list[str]
    suggested_fix: str


async def critique(
    query: str,
    answer: str,
    citations: list[dict],
    evidence: list[EvidenceChunk],
    context: dict[str, Any] | None = None,
) -> SelfCriticResult | None:
    """LLM critiques answer. Returns None on error (treated as pass)."""
    settings = get_settings()
    if not getattr(settings, "self_critic_enabled", False):
        return None

    evidence_preview = "\n".join(
        f"- [{e.chunk_id}] ({e.doc_type or 'unknown'}) {(e.snippet or e.full_text or '')[:180]}..."
        for e in evidence[:8]
    )

    user_parts = [
        f"Query: {query}",
        "",
        "Answer:",
        answer[:1800],
        "",
        f"Citations count: {len(citations)}",
        "",
        "Evidence preview:",
        evidence_preview,
    ]

    critic_context = dict(context or {})
    critic_context["require_completeness"] = bool(
        getattr(settings, "self_critic_require_completeness", True)
    )
    user_parts.extend([
        "",
        "Evaluation context:",
        json.dumps(critic_context, ensure_ascii=False)[:2500],
    ])
    user_content = "\n".join(user_parts).strip()

    try:
        from app.core.tracing import current_llm_task_var

        current_llm_task_var.set("self_critic")
        llm = get_llm_gateway()
        model = get_model_for_task("self_critic")
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": SELF_CRITIC_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            model=model,
            max_tokens=320,
        )
        content = (resp.content or "").strip()
        if "```json" in content:
            match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
            content = match.group(1) if match else content
        elif "```" in content:
            match = re.search(r"```\s*([\s\S]*?)\s*```", content)
            content = match.group(1) if match else content

        data = json.loads(content)
        pass_ = bool(data.get("pass", True))
        issues = [str(i) for i in data.get("issues", []) if isinstance(i, str)]
        suggested_fix = (data.get("suggested_fix") or "").strip()

        result = SelfCriticResult(pass_=pass_, issues=issues, suggested_fix=suggested_fix)
        try:
            from app.core.metrics import self_critic_total, self_critic_fail_total

            self_critic_total.inc()
            if not result.pass_:
                self_critic_fail_total.inc()
        except Exception:
            pass
        logger.info(
            "self_critic",
            pass_=result.pass_,
            issues_count=len(result.issues),
            issues_preview=result.issues[:2] if result.issues else [],
        )
        return result
    except Exception as e:
        logger.warning("self_critic_failed", error=str(e))
        return None
