"""Evidence Selector – Phase 1: Coverage-aware selection via LLM.

Select minimal evidence set that covers required_evidence and maximizes relevance.
Replaces fixed top-k with LLM-driven selection.
"""

from __future__ import annotations

import json
import math
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
3. Prefer structured docs (howto, docs, faq, policy, pricing, tos) over conversation when both exist. When structured docs include multiple options/paths (e.g. different access methods, plans, steps), keep them—do not replace with conversation anecdotes that cover fewer options.
4. Prefer diverse doc_types and diverse plans/products (avoid over-concentrating on one plan type)
5. Preserve at most one relevant conversation chunk when docs do not sufficiently answer the query

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
- Prefer structured docs over conversation. If structured docs include steps/options/policies relevant to the query, keep them and avoid replacing them with conversation anecdotes. When evidence has multiple distinct options (e.g. different methods, plans, paths), ensure selection covers them—completeness over brevity.
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


def _structured_doc_types_from_settings() -> set[str]:
    raw = str(getattr(get_settings(), "evidence_selector_structured_doc_types", "") or "").strip()
    if not raw:
        return {"howto", "docs", "faq", "policy", "tos", "pricing"}
    out = {
        part.strip().lower()
        for part in raw.split(",")
        if part and part.strip()
    }
    return out or {"howto", "docs", "faq", "policy", "tos", "pricing"}


def _is_structured_doc(doc_type: str, structured_doc_types: set[str]) -> bool:
    return str(doc_type or "").strip().lower() in structured_doc_types


def _find_lowest_score_index(
    selected: list[tuple[SearchChunk, float]],
    *,
    predicate,
) -> int | None:
    candidates = [
        (idx, score)
        for idx, (_, score) in enumerate(selected)
        if predicate(selected[idx][0], score)
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda item: item[1])[0]


def _rebalance_structured_selection(
    selected: list[tuple[SearchChunk, float]],
    candidates: list[tuple[SearchChunk, float]],
) -> list[tuple[SearchChunk, float]]:
    if not selected:
        return selected
    settings = get_settings()
    structured_doc_types = _structured_doc_types_from_settings()
    conversation_cap = int(getattr(settings, "evidence_selector_conversation_cap", 1) or 1)
    conversation_cap = max(0, conversation_cap)
    try:
        min_structured_share = float(
            getattr(settings, "evidence_selector_min_structured_share", 0.6) or 0.6
        )
    except Exception:
        min_structured_share = 0.6
    min_structured_share = max(0.0, min(1.0, min_structured_share))

    selected_items = list(selected)
    selected_ids = {chunk.chunk_id for chunk, _ in selected_items}
    structured_pool: list[tuple[SearchChunk, float]] = [
        (chunk, score)
        for chunk, score in candidates
        if _is_structured_doc(chunk.doc_type or "", structured_doc_types)
        and chunk.chunk_id not in selected_ids
    ]

    def _take_next_structured() -> tuple[SearchChunk, float] | None:
        if not structured_pool:
            return None
        return structured_pool.pop(0)

    while True:
        conversation_selected = [
            (idx, item)
            for idx, item in enumerate(selected_items)
            if (item[0].doc_type or "").strip().lower() == "conversation"
        ]
        if len(conversation_selected) <= conversation_cap:
            break
        replacement = _take_next_structured()
        if replacement is None:
            break
        remove_idx = min(conversation_selected, key=lambda pair: pair[1][1])[0]
        selected_ids.discard(selected_items[remove_idx][0].chunk_id)
        selected_items[remove_idx] = replacement
        selected_ids.add(replacement[0].chunk_id)

    structured_count = sum(
        1
        for chunk, _ in selected_items
        if _is_structured_doc(chunk.doc_type or "", structured_doc_types)
    )
    target_structured = min(
        len(selected_items),
        max(0, math.ceil(len(selected_items) * min_structured_share)),
    )
    while structured_count < target_structured:
        replacement = _take_next_structured()
        if replacement is None:
            break
        remove_idx = _find_lowest_score_index(
            selected_items,
            predicate=lambda chunk, score: not _is_structured_doc(chunk.doc_type or "", structured_doc_types),
        )
        if remove_idx is None:
            break
        selected_ids.discard(selected_items[remove_idx][0].chunk_id)
        selected_items[remove_idx] = replacement
        selected_ids.add(replacement[0].chunk_id)
        structured_count += 1

    return selected_items


def _coverage_mapping_allowed(requirement: str, chunk: SearchChunk) -> bool:
    req = str(requirement or "").strip().lower()
    doc_type = (chunk.doc_type or "").strip().lower()
    if req == "policy_language":
        configured_policy_types = {
            str(t).strip().lower()
            for t in (get_settings().reviewer_policy_doc_types or [])
            if str(t).strip()
        }
        if configured_policy_types:
            return doc_type in configured_policy_types
        return doc_type in {"policy", "tos"}
    if req == "steps_structure":
        return doc_type in {"howto", "docs", "faq", "conversation"}
    return req in {"numbers_units", "transaction_link", "has_any_url"}


def _validate_coverage_map(
    coverage_map: dict[str, str],
    *,
    selected: list[tuple[SearchChunk, float]],
    required_evidence: list[str],
) -> dict[str, str]:
    selected_by_id = {chunk.chunk_id: chunk for chunk, _ in selected}
    allowed_requirements = {str(req).strip() for req in required_evidence if str(req).strip()}
    validated: dict[str, str] = {}
    for req, cid in coverage_map.items():
        if req not in allowed_requirements:
            continue
        chunk = selected_by_id.get(str(cid))
        if not chunk:
            continue
        if not _coverage_mapping_allowed(req, chunk):
            continue
        validated[req] = chunk.chunk_id
    return validated


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
        selected = _rebalance_structured_selection(
            reranked[:top_k_fallback],
            reranked[:20],
        )
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
        selected = _rebalance_structured_selection(selected, candidates)
        coverage_map = _validate_coverage_map(
            coverage_map,
            selected=selected,
            required_evidence=req_list,
        )
        uncovered = [req for req in req_list if req not in coverage_map]

        return EvidenceSelectionResult(
            selected=selected,
            coverage_map=coverage_map,
            uncovered_requirements=uncovered,
            reasoning=reasoning,
            used_llm=True,
        )

    except Exception as e:
        logger.warning("evidence_selector_llm_failed", error=str(e))
        selected = _rebalance_structured_selection(
            candidates[:top_k_fallback],
            candidates,
        )
        return EvidenceSelectionResult(
            selected=selected,
            coverage_map={},
            uncovered_requirements=req_list,
            reasoning=f"fallback: {str(e)[:50]}",
            used_llm=False,
        )
