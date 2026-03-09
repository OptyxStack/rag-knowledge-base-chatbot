"""Ingestion pipeline: clean, chunk, embed, index."""

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from html import unescape
from typing import Any

from bs4 import BeautifulSoup
import tiktoken

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import Document, Chunk
from app.search.embeddings import get_embedding_provider
from app.search.opensearch_client import OpenSearchClient
from app.search.qdrant_client import QdrantSearchClient

logger = get_logger(__name__)


def _checksum(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _clean_html(html: str, base_url: str | None = None) -> str:
    """Strip boilerplate and extract text from HTML. Preserves links as text when base_url given."""
    from urllib.parse import urljoin, urlparse

    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "nav", "footer", "header"]):
        tag.decompose()

    # Preserve heading structure as markdown-like markers so chunking can keep semantic boundaries.
    for heading in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6"]):
        text = heading.get_text(" ", strip=True)
        if not text:
            continue
        level = int(heading.name[1]) if heading.name and len(heading.name) == 2 else 2
        level = max(1, min(level, 6))
        heading.replace_with(f"\n{'#' * level} {text}\n")

    if base_url:
        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if not href or href.startswith(("#", "javascript:", "mailto:")):
                continue
            try:
                full_url = urljoin(base_url, href)
                parsed = urlparse(full_url)
                if parsed.scheme in ("http", "https"):
                    link_text = a.get_text(strip=True) or full_url
                    a.replace_with(f"{link_text} ({full_url})")
            except Exception:
                pass

    text = soup.get_text(separator="\n")
    text = unescape(text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" +", " ", text)
    return text.strip()


def _get_tokenizer():
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        return tiktoken.get_encoding("gpt2")


def _count_tokens(text: str, *, enc=None) -> int:
    tokenizer = enc or _get_tokenizer()
    return len(tokenizer.encode(text or ""))


@dataclass
class PreparedChunk:
    chunk_text: str
    headings: str
    parent_ref: str | None = None
    parent_heading: str | None = None


def _chunk_by_semantic_boundaries(
    text: str,
    min_tokens: int = 300,
    max_tokens: int = 700,
) -> list[tuple[str, str]]:
    """Chunk text by headings/paragraphs into semantic chunks."""
    if not text.strip():
        return []

    max_tokens = max(max_tokens, 1)
    min_tokens = max(1, min(min_tokens, max_tokens))
    enc = _get_tokenizer()

    def _split_oversized(block: str) -> list[str]:
        if _count_tokens(block, enc=enc) <= max_tokens:
            return [block]
        sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", block) if s.strip()]
        if not sentences:
            sentences = [w.strip() for w in re.split(r"\s+", block) if w.strip()]
        parts: list[str] = []
        current: list[str] = []
        current_tokens = 0
        for sentence in sentences:
            sentence_tokens = _count_tokens(sentence, enc=enc)
            if current and current_tokens + sentence_tokens > max_tokens:
                parts.append(" ".join(current).strip())
                current = [sentence]
                current_tokens = sentence_tokens
            else:
                current.append(sentence)
                current_tokens += sentence_tokens
        if current:
            parts.append(" ".join(current).strip())
        return [p for p in parts if p]

    blocks = re.split(r"\n\s*\n+", text)
    normalized_blocks: list[str] = []
    for block in blocks:
        raw = (block or "").strip()
        if not raw:
            continue
        normalized_blocks.extend(_split_oversized(raw))

    chunks: list[tuple[str, str]] = []
    current_parts: list[str] = []
    current_tokens = 0
    current_heading = ""

    for block in normalized_blocks:
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", block)
        heading_title = heading_match.group(2).strip() if heading_match else ""

        # Start a new chunk at heading boundaries when current chunk is already meaningful.
        if heading_title and current_parts and current_tokens >= min_tokens:
            chunks.append(("\n\n".join(current_parts).strip(), current_heading))
            current_parts = []
            current_tokens = 0

        if heading_title:
            current_heading = heading_title

        block_tokens = _count_tokens(block, enc=enc)
        should_flush = (
            current_parts
            and current_tokens >= min_tokens
            and current_tokens + block_tokens > max_tokens
        )
        if should_flush:
            chunks.append(("\n\n".join(current_parts).strip(), current_heading))
            current_parts = [block]
            current_tokens = block_tokens
        else:
            current_parts.append(block)
            current_tokens += block_tokens

    if current_parts:
        chunks.append(("\n\n".join(current_parts).strip(), current_heading))

    return [(chunk_text, headings) for chunk_text, headings in chunks if chunk_text]


def _expand_to_semantic_units(
    parent_chunks: list[tuple[str, str]],
    *,
    unit_min_tokens: int,
    unit_max_tokens: int,
    include_parent_refs: bool,
) -> list[PreparedChunk]:
    """Split parent chunks into smaller semantic units and keep optional parent refs."""
    units: list[PreparedChunk] = []
    parent_total = len(parent_chunks)
    for parent_idx, (parent_text, parent_heading) in enumerate(parent_chunks):
        child_chunks = _chunk_by_semantic_boundaries(
            parent_text,
            min_tokens=unit_min_tokens,
            max_tokens=unit_max_tokens,
        )
        if not child_chunks:
            child_chunks = [(parent_text, parent_heading)]

        parent_ref = None
        if include_parent_refs:
            parent_ref = f"p{parent_idx}:{_checksum(parent_text)[:12]}"

        for child_text, child_heading in child_chunks:
            units.append(
                PreparedChunk(
                    chunk_text=child_text,
                    headings=child_heading or parent_heading,
                    parent_ref=parent_ref,
                    parent_heading=parent_heading or None,
                )
            )

    if units:
        logger.info(
            "ingestion_semantic_chunking",
            parent_chunks=parent_total,
            semantic_units=len(units),
            parent_refs=include_parent_refs,
        )
    return units


def prepare_document(doc: dict[str, Any]) -> tuple[str, str, list[PreparedChunk]]:
    """Clean and chunk a document. Returns (cleaned_content, raw_content, chunks)."""
    raw = doc.get("raw_text") or doc.get("raw_html") or doc.get("content", "")
    base_url = doc.get("url") or doc.get("source_url")
    if doc.get("raw_html") or "<" in raw[:100]:
        cleaned = _clean_html(raw, base_url=base_url)
    else:
        cleaned = raw

    settings = get_settings()
    parent_chunks = _chunk_by_semantic_boundaries(
        cleaned,
        min_tokens=settings.chunk_min_tokens,
        max_tokens=settings.chunk_max_tokens,
    )
    semantic_min = max(1, settings.chunk_semantic_min_tokens)
    semantic_max = max(semantic_min, settings.chunk_semantic_max_tokens)
    chunks = _expand_to_semantic_units(
        parent_chunks,
        unit_min_tokens=semantic_min,
        unit_max_tokens=semantic_max,
        include_parent_refs=settings.chunk_parent_refs_enabled,
    )
    return cleaned, raw, chunks


class IngestionService:
    """Orchestrates document ingestion: clean, chunk, store, embed, index."""

    def __init__(
        self,
        opensearch: OpenSearchClient | None = None,
        qdrant: QdrantSearchClient | None = None,
        embedder=None,
    ) -> None:
        self._settings = get_settings()
        self._opensearch = opensearch or OpenSearchClient()
        self._qdrant = qdrant or QdrantSearchClient()
        self._embedder = embedder or get_embedding_provider()

    async def ingest_document(self, doc: dict[str, Any], db_session) -> str | None:
        """Ingest a single document. Returns document_id or None if skipped (idempotent)."""
        url = doc.get("url") or doc.get("source_url")
        if not url:
            logger.warning("ingest_skipped_no_url")
            return None

        title = doc.get("title", "Untitled")
        doc_type = doc.get("doc_type", "other")
        effective_date = doc.get("effective_date") or doc.get("last_updated")
        metadata = doc.get("metadata")
        source_file = doc.get("source_file")
        if isinstance(effective_date, str):
            try:
                effective_date = datetime.fromisoformat(effective_date.replace("Z", "+00:00"))
            except ValueError:
                effective_date = None

        cleaned, raw, prepared_chunks = prepare_document(doc)
        checksum = _checksum(cleaned)

        # Optional: store raw doc in object storage
        if raw:
            try:
                from app.core.storage import get_storage
                storage = get_storage()
                if storage._get_client():
                    key = f"raw/{_checksum(url)}.txt"
                    body = raw.encode("utf-8") if isinstance(raw, str) else raw
                    storage.put(key, body, "text/plain")
            except Exception:
                pass

        # Idempotency: check existing by source_url
        from sqlalchemy import select
        from app.db.models import Document as DocModel

        result = await db_session.execute(select(DocModel).where(DocModel.source_url == url))
        existing = result.scalars().first()
        if existing and existing.checksum == checksum:
            logger.info("ingest_skipped_unchanged", source_url=url)
            # Content unchanged: skip re-chunk/re-embed but still update doc_type, title (e.g. from classifier)
            existing.title = title
            existing.doc_type = doc_type
            existing.effective_date = effective_date
            existing.doc_metadata = metadata
            existing.source_file = source_file
            existing.updated_at = datetime.utcnow()
            await db_session.flush()
            return existing.id

        # Create or update document
        if existing:
            document_id = existing.id
            # Fetch old chunk IDs before delete (for search index cleanup)
            from sqlalchemy import delete, select
            from app.db.models import Chunk as ChunkModel
            old_chunks_result = await db_session.execute(select(ChunkModel.id).where(ChunkModel.document_id == document_id))
            old_chunk_ids = list(old_chunks_result.scalars().all())
            for cid in old_chunk_ids:
                await self._opensearch.delete_chunk(cid)
                self._qdrant.delete_chunk(cid)
            existing.title = title
            existing.doc_type = doc_type
            existing.effective_date = effective_date
            existing.checksum = checksum
            existing.raw_content = raw
            existing.cleaned_content = cleaned
            existing.doc_metadata = metadata
            existing.source_file = source_file
            existing.updated_at = datetime.utcnow()
            await db_session.execute(delete(ChunkModel).where(ChunkModel.document_id == document_id))
        else:
            new_doc = Document(
                title=title,
                source_url=url,
                doc_type=doc_type,
                effective_date=effective_date,
                checksum=checksum,
                raw_content=raw,
                cleaned_content=cleaned,
                doc_metadata=metadata,
                source_file=source_file,
            )
            db_session.add(new_doc)
            await db_session.flush()
            document_id = new_doc.id

        # Ensure search indices exist
        await self._opensearch.ensure_index()
        self._qdrant.ensure_collection(self._embedder.dimensions())

        # Create chunks and index
        for idx, prepared in enumerate(prepared_chunks):
            chunk_text = prepared.chunk_text
            headings = prepared.headings
            token_count = _count_tokens(chunk_text)
            chunk_checksum = _checksum(chunk_text)

            chunk_meta = {"headings": headings, "doc_type": doc_type}
            if prepared.parent_ref:
                chunk_meta["parent_ref"] = prepared.parent_ref
            if prepared.parent_heading:
                chunk_meta["parent_heading"] = prepared.parent_heading
            if metadata:
                chunk_meta.update({k: v for k, v in metadata.items() if k in ("product", "category", "key_points") and v is not None})
            chunk = Chunk(
                document_id=document_id,
                chunk_index=idx,
                chunk_text=chunk_text,
                token_count=token_count,
                chunk_metadata=chunk_meta,
                checksum=chunk_checksum,
            )
            db_session.add(chunk)
            await db_session.flush()

            # Embed and index
            vectors = await self._embedder.embed([chunk_text])
            qdrant_meta = {"headings": headings}
            if prepared.parent_ref:
                qdrant_meta["parent_ref"] = prepared.parent_ref
            if prepared.parent_heading:
                qdrant_meta["parent_heading"] = prepared.parent_heading
            if metadata:
                qdrant_meta.update({k: v for k, v in metadata.items() if k in ("product", "category") and v is not None})
            self._qdrant.upsert_chunk(
                chunk_id=chunk.id,
                vector=vectors[0],
                chunk_text=chunk_text,
                document_id=document_id,
                source_url=url,
                doc_type=doc_type,
                metadata=qdrant_meta,
            )
            await self._opensearch.index_chunk(
                chunk_id=chunk.id,
                document_id=document_id,
                title=title,
                headings=headings,
                body=chunk_text,
                doc_type=doc_type,
                source_url=url,
                effective_date=effective_date.isoformat() if effective_date else None,
                chunk_text=chunk_text,
            )

        await db_session.commit()
        logger.info("ingest_complete", document_id=document_id, chunks=len(prepared_chunks))
        return document_id
