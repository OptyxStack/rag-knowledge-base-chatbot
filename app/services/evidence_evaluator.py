"""LLM Evidence Evaluator – archi_v3. Advises Retry Planner, does not override deterministic gates."""

import json
import re
from dataclasses import dataclass, field

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk
from app.services.llm_gateway import get_llm_gateway
from app.services.model_router import get_model_for_task
from app.services.schemas import QuerySpec

logger = get_logger(__name__)

EVIDENCE_EVALUATOR_PROMPT = """You evaluate whether retrieved evidence is relevant to the user's query.

Output JSON only, no markdown:
{
  "relevance_score": 0.0-1.0,
  "coverage_gaps": ["missing X", "missing Y"],
  "retry_needed": false,
  "suggested_query": "alternative query if retry_needed",
  "retry_boost_terms": [],
  "retry_doc_types": []
}

relevance_score: 0-1, how relevant is the evidence to the query?
coverage_gaps: what specific info is missing (e.g. "missing pricing", "missing SLA details")?
retry_needed: true if evidence is insufficient or irrelevant and a different search might help
suggested_query: when retry_needed, suggest a clearer query for retrieval (e.g. add keywords)
retry_boost_terms: when retry_needed, optional terms to boost search (e.g. ["pricing", "USD"]). Empty [] if not needed.
retry_doc_types: when retry_needed, optional doc types to search (e.g. ["pricing", "faq"]). Empty [] to keep default."""


@dataclass
class EvidenceEvalResult:
    """Evidence evaluator output – advises only."""

    relevance_score: float
    coverage_gaps: list[str]
    retry_needed: bool
    suggested_query: str | None
    retry_boost_terms: list[str] = field(default_factory=list)
    retry_doc_types: list[str] = field(default_factory=list)


async def evaluate_evidence(
    query: str,
    query_spec: QuerySpec | None,
    evidence: list[EvidenceChunk],
    top_n: int = 5,
) -> EvidenceEvalResult | None:
    """LLM evaluates evidence relevance. Returns None on error (caller uses Retry Planner as-is)."""
    if not getattr(get_settings(), "evidence_evaluator_enabled", False):
        return None

    if not evidence:
        return EvidenceEvalResult(0.0, ["no_evidence"], True, query, [], [])

    summaries = []
    for i, e in enumerate(evidence[:top_n], 1):
        text = (e.full_text or e.snippet or "")[:200]
        summaries.append(f"[{i}] {e.source_url or '?'}: {text}...")

    user_content = f"Query: {query}\n\nEvidence summaries:\n" + "\n".join(summaries)

    try:
        from app.core.tracing import current_llm_task_var
        current_llm_task_var.set("evidence_evaluator")
        llm = get_llm_gateway()
        model = get_model_for_task("evidence_evaluator")
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": EVIDENCE_EVALUATOR_PROMPT},
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
        score = float(data.get("relevance_score", 0.5))
        gaps = [str(g) for g in data.get("coverage_gaps", []) if isinstance(g, str)]
        retry = bool(data.get("retry_needed", False))
        suggested = (data.get("suggested_query") or "").strip() or None
        boost = [str(t) for t in data.get("retry_boost_terms", []) if isinstance(t, str)][:8]
        doc_types = [str(t) for t in data.get("retry_doc_types", []) if isinstance(t, str)][:5]

        result = EvidenceEvalResult(
            relevance_score=max(0.0, min(1.0, score)),
            coverage_gaps=gaps[:5],
            retry_needed=retry,
            suggested_query=suggested,
            retry_boost_terms=boost,
            retry_doc_types=doc_types,
        )
        try:
            from app.core.metrics import (
                evidence_evaluator_total,
                evidence_evaluator_retry_needed,
                evidence_evaluator_relevance_score,
            )
            evidence_evaluator_total.inc()
            evidence_evaluator_relevance_score.observe(result.relevance_score)
            if result.retry_needed:
                evidence_evaluator_retry_needed.inc()
        except Exception:
            pass
        logger.info(
            "evidence_evaluator",
            relevance_score=round(result.relevance_score, 2),
            retry_needed=result.retry_needed,
            coverage_gaps=result.coverage_gaps[:3],
            suggested_query_preview=result.suggested_query[:80] if result.suggested_query else None,
        )
        return result
    except Exception as e:
        logger.warning("evidence_evaluator_failed", error=str(e))
        return None
