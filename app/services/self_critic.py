"""LLM Self-Critic – archi_v3. Checks answer quality; suggests regenerate on fail."""

import json
import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk
from app.services.llm_gateway import get_llm_gateway

logger = get_logger(__name__)

SELF_CRITIC_PROMPT = """You are a quality reviewer for a support chatbot answer.

Check if the answer is grounded in the evidence. Output JSON only:
{
  "pass": true,
  "issues": [],
  "suggested_fix": ""
}

pass: false if any of: unsupported claims, incomplete answer, missing critical info, overgeneralization, hallucination
issues: list of specific problems (e.g. "Claim X not in evidence", "Missing pricing for Plan Y")
suggested_fix: brief instruction for regeneration when pass=false (e.g. "Add specific prices from evidence")"""


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
) -> SelfCriticResult | None:
    """LLM critiques answer. Returns None on error (treat as pass)."""
    if not getattr(get_settings(), "self_critic_enabled", False):
        return None

    evidence_preview = "\n".join(
        f"- [{e.chunk_id}] {(e.snippet or e.full_text or '')[:150]}..."
        for e in evidence[:5]
    )

    user_content = f"""Query: {query}

Answer:
{answer[:1500]}

Citations: {len(citations)}

Evidence preview:
{evidence_preview}"""

    try:
        llm = get_llm_gateway()
        model = getattr(get_settings(), "llm_model", "gpt-4o-mini")
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": SELF_CRITIC_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            model=model,
            max_tokens=256,
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
