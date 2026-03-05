"""Chunk filter – before generate: LLM selects relevant chunks for the question."""

from __future__ import annotations

import json

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk

logger = get_logger(__name__)

CHUNK_FILTER_PROMPT = """Given a user question and a list of evidence chunks, select which chunks are relevant to answer the question.

Output JSON only, no markdown:
{
  "relevant_chunk_ids": ["chunk_id_1", "chunk_id_2", ...]
}

Rules:
- Include only chunks that directly help answer the question.
- Exclude irrelevant or redundant chunks.
- Keep at least 1 chunk if any exists. Return empty array only when no chunk is useful."""


def _extract_json(text: str) -> str:
    text = (text or "").strip()
    if "```json" in text:
        start = text.find("```json") + 7
        end = text.find("```", start)
        if end > start:
            return text[start:end].strip()
    if "```" in text:
        start = text.find("```") + 3
        end = text.find("```", start)
        if end > start:
            return text[start:end].strip()
    return text


async def filter_chunks_for_query(
    query: str,
    chunks: list[EvidenceChunk],
) -> list[EvidenceChunk]:
    """LLM selects chunks relevant to the question. Returns filtered list."""
    settings = get_settings()
    if not getattr(settings, "chunk_filter_enabled", True):
        return chunks

    if not chunks or len(chunks) <= 6:
        return chunks

    summaries = []
    chunk_by_id: dict[str, EvidenceChunk] = {}
    for i, c in enumerate(chunks[:15], 1):
        text = (c.full_text or c.snippet or "")[:300]
        if len((c.full_text or c.snippet) or "") > 300:
            text += "..."
        summaries.append(f"[{c.chunk_id}] {c.doc_type or '?'} | {c.source_url or '?'}\n  {text}")
        chunk_by_id[c.chunk_id] = c

    user_content = f"Question: {query[:400]}\n\nChunks:\n" + "\n\n".join(summaries)

    try:
        from app.core.tracing import current_llm_task_var
        from app.services.llm_gateway import get_llm_gateway
        from app.services.model_router import get_model_for_task

        current_llm_task_var.set("chunk_filter")
        llm = get_llm_gateway()
        model = get_model_for_task("chunk_filter")
        resp = await llm.chat(
            messages=[
                {"role": "system", "content": CHUNK_FILTER_PROMPT},
                {"role": "user", "content": user_content},
            ],
            temperature=0.0,
            model=model,
            max_tokens=256,
        )
        text = _extract_json((resp.content or "").strip())
        data = json.loads(text)
        raw_ids = data.get("relevant_chunk_ids") or []

        valid_ids = [cid for cid in raw_ids if cid in chunk_by_id]
        if not valid_ids:
            logger.warning("chunk_filter_no_valid_ids", raw_ids=raw_ids[:5])
            return chunks

        seen = set()
        filtered = []
        for cid in valid_ids:
            if cid not in seen:
                filtered.append(chunk_by_id[cid])
                seen.add(cid)

        logger.info("chunk_filter", original=len(chunks), filtered=len(filtered), ids=valid_ids[:5])
        return filtered

    except Exception as e:
        logger.warning("chunk_filter_failed", error=str(e), query=query[:50])
        return chunks
