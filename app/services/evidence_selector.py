"""Evidence Selector – Phase 1: Coverage-aware selection via LLM.

Select minimal evidence set that covers required_evidence and maximizes relevance.
Replaces fixed top-k with LLM-driven selection.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import SearchChunk
from app.services.llm_gateway import get_llm_gateway
from app.services.model_router import get_model_for_task

logger = get_logger(__name__)

EVIDENCE_SELECTOR_PROMPT = """You select evidence chunks for a support RAG system.

Given a query, candidate chunks (with IDs), and required evidence types, select chunks that:
1. Covers all required_evidence when possible (numbers, links, policy, steps)
2. Maximizes relevance to the query
3. Prefer diverse doc_types and diverse plans/products (avoid over-concentrating on one plan type)

Required evidence types:
- numbers_units: price, cost, specs with numbers
- has_any_url / transaction_link: order/store/checkout links
- policy_language: refund, terms, policy clauses
- steps_structure: how-to, setup steps

Output JSON only, no markdown:
{
  "selected_chunk_ids": ["chunk_id_1", "chunk_id_2", ...],
  "coverage_map": {"numbers_units": "chunk_id", "transaction_link": "chunk_id", ...},
  "uncovered_requirements": [],
  "reasoning": "brief"
}

Rules:
- selected_chunk_ids: subset of provided chunk IDs, in order of importance
- coverage_map: requirement -> chunk_id that best satisfies it (optional, can be partial)
- uncovered_requirements: requirements no chunk satisfies
- Prefer diversity across doc_types and plan/product lines when candidates show multiple options. Do not treat different plans as redundant.
- Select 6-12 chunks based on query complexity and how many distinct options candidates offer.
- Only use chunk IDs from the candidate list. Do not invent IDs."""


@dataclass
class EvidenceSelectionResult:
    """Result from LLM evidence selector."""

    selected: list[tuple[SearchChunk, float]]
    coverage_map: dict[str, str]
    uncovered_requirements: list[str]
    reasoning: str = ""
    used_llm: bool = False


async def select_evidence_for_query(
    query: str,
    reranked: list[tuple[SearchChunk, float]],
    required_evidence: list[str] | None = None,
    product_type: str | None = None,
    top_k_fallback: int = 8,
) -> EvidenceSelectionResult:
    """Select evidence chunks by coverage and relevance. LLM when enabled, else top-k.

    Args:
        query: User query
        reranked: Reranked chunks (chunk, score) from retrieval
        required_evidence: Required evidence types (numbers_units, transaction_link, etc.)
        top_k_fallback: Fallback count when LLM disabled or fails

    Returns:
        EvidenceSelectionResult with selected chunks, coverage_map, etc.
    """
    settings = get_settings()
    use_llm = getattr(settings, "evidence_selector_use_llm", True)

    if not reranked:
        return EvidenceSelectionResult(
            selected=[],
            coverage_map={},
            uncovered_requirements=list(required_evidence or []),
            used_llm=False,
        )

    if not use_llm:
        selected = reranked[:top_k_fallback]
        return EvidenceSelectionResult(
            selected=selected,
            coverage_map={},
            uncovered_requirements=[],
            reasoning="top_k_fallback",
            used_llm=False,
        )

    # Limit candidates for LLM context (top 15-20)
    candidates = reranked[:20]
    req_list = list(dict.fromkeys(required_evidence or []))

    chunk_summaries = []
    chunk_by_id: dict[str, tuple[SearchChunk, float]] = {}
    for i, (chunk, score) in enumerate(candidates, 1):
        text = (chunk.chunk_text or "")[:250]
        if len(chunk.chunk_text or "") > 250:
            text += "..."
        chunk_summaries.append(
            f"[{chunk.chunk_id}] (score={score:.2f}) {chunk.doc_type or '?'} | {chunk.source_url or '?'}\n  {text}"
        )
        chunk_by_id[chunk.chunk_id] = (chunk, score)

    user_parts = [
        f"Query: {query[:400]}",
        f"Candidate chunks:\n" + "\n".join(chunk_summaries),
    ]
    if req_list:
        user_parts.append(f"Required evidence: {req_list}")
    if product_type:
        user_parts.append(f"Query context: product_type={product_type}")

    user_content = "\n\n".join(user_parts)

    try:
        model = get_model_for_task("evidence_selector")
        llm = get_llm_gateway()
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": EVIDENCE_SELECTOR_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            model=model,
            max_tokens=512,
        )
        content = (resp.content or "").strip()
        if "```json" in content:
            match = re.search(r"```json\s*([\s\S]*?)\s*```", content)
            content = match.group(1) if match else content
        elif "```" in content:
            match = re.search(r"```\s*([\s\S]*?)\s*```", content)
            content = match.group(1) if match else content

        data = json.loads(content)
        raw_ids = data.get("selected_chunk_ids") or []
        coverage_map = dict(data.get("coverage_map") or {})
        uncovered = [str(x) for x in data.get("uncovered_requirements") or []]
        reasoning = str(data.get("reasoning") or "")[:200]

        # Filter to valid IDs, preserve order from reranked
        valid_ids = {cid for cid in raw_ids if cid in chunk_by_id}
        if not valid_ids:
            logger.warning("evidence_selector_no_valid_ids", raw_ids=raw_ids[:5])
            selected = candidates[:top_k_fallback]
        else:
            # Order by raw_ids; trust LLM selection (minimal set)
            seen = set()
            selected = []
            for cid in raw_ids:
                if cid in chunk_by_id and cid not in seen:
                    selected.append(chunk_by_id[cid])
                    seen.add(cid)

        return EvidenceSelectionResult(
            selected=selected,
            coverage_map=coverage_map,
            uncovered_requirements=uncovered,
            reasoning=reasoning,
            used_llm=True,
        )

    except Exception as e:
        logger.warning("evidence_selector_llm_failed", error=str(e))
        selected = candidates[:top_k_fallback]
        return EvidenceSelectionResult(
            selected=selected,
            coverage_map={},
            uncovered_requirements=req_list,
            reasoning=f"fallback: {str(e)[:50]}",
            used_llm=False,
        )
