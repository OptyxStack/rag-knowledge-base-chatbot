"""Hybrid retrieval: BM25 + vector + rerank. Workstream 3: CandidatePool → EvidenceSet."""

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import EvidenceChunk, SearchChunk
from app.search.embeddings import get_embedding_provider
from app.search.opensearch_client import OpenSearchClient
from app.search.qdrant_client import QdrantSearchClient
from app.search.reranker import RerankerProvider, get_reranker_provider

from app.services.evidence_set_builder import build_evidence_set
from app.services.retrieval_planner import build_retrieval_plan_for_attempt
from app.services.retry_planner import RetryStrategy
from app.services.schemas import CandidateChunk, CandidatePool, EvidenceSet, QuerySpec, RetrievalPlan

logger = get_logger(__name__)


@dataclass
class EvidencePack:
    """Retrieved evidence for answer generation. Workstream 3: includes plan, pool, evidence_set."""

    chunks: list[EvidenceChunk] = field(default_factory=list)
    retrieval_stats: dict[str, Any] = field(default_factory=dict)
    retrieval_plan: RetrievalPlan | None = None
    candidate_pool: CandidatePool | None = None
    evidence_set: EvidenceSet | None = None


@dataclass
class QueryRewrite:
    """Query rewrite for dual retrieval."""

    keyword_query: str
    semantic_query: str
    retrieval_profile: str | None = None  # From LLM rewriter when QuerySpec absent


@dataclass
class _Bm25Bundle:
    primary: list[SearchChunk] = field(default_factory=list)
    extra: list[SearchChunk] = field(default_factory=list)
    supporting: list[SearchChunk] = field(default_factory=list)
    diversity: list[SearchChunk] = field(default_factory=list)


@dataclass
class _VectorBundle:
    primary: list[SearchChunk] = field(default_factory=list)
    supporting: list[SearchChunk] = field(default_factory=list)


class RetrievalService:
    """Hybrid retrieval with query rewrite, merge, and rerank."""

    def __init__(
        self,
        opensearch: OpenSearchClient | None = None,
        qdrant: QdrantSearchClient | None = None,
        embedding_provider=None,
        reranker: RerankerProvider | None = None,
    ) -> None:
        self._settings = get_settings()
        self._opensearch = opensearch or OpenSearchClient()
        self._qdrant = qdrant or QdrantSearchClient()
        self._embedder = embedding_provider or get_embedding_provider()
        self._reranker = reranker or get_reranker_provider()
        self._opensearch_timeout_s = self._float_setting("retrieval_opensearch_timeout_seconds", 6.0)
        self._qdrant_timeout_s = self._float_setting("retrieval_qdrant_timeout_seconds", 6.0)
        self._embedding_timeout_s = self._float_setting("retrieval_embedding_timeout_seconds", 8.0)
        self._opensearch_semaphore = asyncio.Semaphore(
            self._int_setting("retrieval_opensearch_max_concurrency", 24)
        )
        self._qdrant_semaphore = asyncio.Semaphore(
            self._int_setting("retrieval_qdrant_max_concurrency", 24)
        )
        self._embedding_semaphore = asyncio.Semaphore(
            self._int_setting("retrieval_embedding_max_concurrency", 24)
        )

    def _int_setting(self, name: str, default: int) -> int:
        try:
            value = int(getattr(self._settings, name, default))
        except (TypeError, ValueError):
            return default
        return max(1, value)

    def _float_setting(self, name: str, default: float) -> float:
        try:
            value = float(getattr(self._settings, name, default))
        except (TypeError, ValueError):
            return default
        return max(0.1, value)

    async def _search_opensearch_safe(
        self,
        *,
        query: str,
        top_n: int,
        doc_types: list[str] | None,
        boost_pricing: bool,
        page_kinds: list[str] | None = None,
        product_families: list[str] | None = None,
        page_kind_weights: dict[str, float] | None = None,
        product_family_weights: dict[str, float] | None = None,
    ) -> list[SearchChunk]:
        kwargs: dict[str, Any] = {
            "query": query,
            "top_n": top_n,
            "doc_types": doc_types,
            "boost_pricing": boost_pricing,
            "prefer_snippet": False,
            "page_kinds": page_kinds,
            "product_families": product_families,
            "page_kind_weights": page_kind_weights,
            "product_family_weights": product_family_weights,
        }

        async def _run_search(search_kwargs: dict[str, Any]) -> list[SearchChunk]:
            async with self._opensearch_semaphore:
                return await asyncio.wait_for(
                    self._opensearch.search(**search_kwargs),
                    timeout=self._opensearch_timeout_s,
                )

        try:
            results = await _run_search(kwargs)
            if results:
                return results
            if page_kinds or product_families:
                relaxed_kwargs = dict(kwargs)
                relaxed_kwargs["page_kinds"] = None
                relaxed_kwargs["product_families"] = None
                relaxed_kwargs["page_kind_weights"] = None
                relaxed_kwargs["product_family_weights"] = None
                results = await _run_search(relaxed_kwargs)
                if results:
                    return results
            if doc_types:
                relaxed_kwargs = dict(kwargs)
                relaxed_kwargs["doc_types"] = None
                relaxed_kwargs["page_kinds"] = None
                relaxed_kwargs["product_families"] = None
                relaxed_kwargs["page_kind_weights"] = None
                relaxed_kwargs["product_family_weights"] = None
                return await _run_search(relaxed_kwargs)
            return results
        except TypeError:
            # Backward compatibility with older/mocked OpenSearch clients.
            fallback_kwargs = {
                "query": query,
                "top_n": top_n,
                "doc_types": doc_types,
                "boost_pricing": boost_pricing,
                "prefer_snippet": False,
            }
            try:
                return await _run_search(fallback_kwargs)
            except Exception as exc:
                logger.warning(
                    "retrieval_opensearch_fallback_empty",
                    error=str(exc),
                    top_n=top_n,
                    doc_types=doc_types or [],
                )
                return []
        except Exception as exc:
            logger.warning(
                "retrieval_opensearch_fallback_empty",
                error=str(exc),
                top_n=top_n,
                doc_types=doc_types or [],
            )
            return []

    async def _search_qdrant_safe(
        self,
        *,
        vector: list[float],
        top_n: int,
        doc_types: list[str] | None,
        page_kinds: list[str] | None = None,
        product_families: list[str] | None = None,
    ) -> list[SearchChunk]:
        try:
            async with self._qdrant_semaphore:
                results = await asyncio.wait_for(
                    asyncio.to_thread(
                        self._qdrant.search,
                        vector=vector,
                        top_n=top_n,
                        doc_types=doc_types,
                        page_kinds=page_kinds,
                        product_families=product_families,
                    ),
                    timeout=self._qdrant_timeout_s,
                )
            if results:
                return results
            if page_kinds or product_families:
                async with self._qdrant_semaphore:
                    results = await asyncio.wait_for(
                        asyncio.to_thread(
                            self._qdrant.search,
                            vector=vector,
                            top_n=top_n,
                            doc_types=doc_types,
                            page_kinds=None,
                            product_families=None,
                        ),
                        timeout=self._qdrant_timeout_s,
                    )
                if results:
                    return results
            if doc_types:
                async with self._qdrant_semaphore:
                    return await asyncio.wait_for(
                        asyncio.to_thread(
                            self._qdrant.search,
                            vector=vector,
                            top_n=top_n,
                            doc_types=None,
                            page_kinds=None,
                            product_families=None,
                        ),
                        timeout=self._qdrant_timeout_s,
                    )
            return results
        except TypeError:
            # Backward compatibility with older/mocked Qdrant clients.
            try:
                async with self._qdrant_semaphore:
                    return await asyncio.wait_for(
                        asyncio.to_thread(
                            self._qdrant.search,
                            vector=vector,
                            top_n=top_n,
                            doc_types=doc_types,
                        ),
                        timeout=self._qdrant_timeout_s,
                    )
            except Exception as exc:
                logger.warning(
                    "retrieval_qdrant_fallback_empty",
                    error=str(exc),
                    top_n=top_n,
                    doc_types=doc_types or [],
                )
                return []
        except Exception as exc:
            logger.warning(
                "retrieval_qdrant_fallback_empty",
                error=str(exc),
                top_n=top_n,
                doc_types=doc_types or [],
            )
            return []

    async def _embed_query_safe(self, semantic_query: str) -> list[float] | None:
        try:
            async with self._embedding_semaphore:
                vectors = await asyncio.wait_for(
                    self._embedder.embed([semantic_query]),
                    timeout=self._embedding_timeout_s,
                )
        except Exception as exc:
            logger.warning("retrieval_embedding_fallback_empty", error=str(exc))
            return None
        if not vectors or not vectors[0]:
            logger.warning("retrieval_embedding_empty_vector")
            return None
        return vectors[0]

    def _merge_simple(
        self,
        bm25_chunks: list[SearchChunk],
        vector_chunks: list[SearchChunk],
    ) -> list[SearchChunk]:
        """Merge and dedupe by chunk_id. Prefer higher score when duplicate."""
        seen: dict[str, SearchChunk] = {}
        for c in bm25_chunks + vector_chunks:
            score = c.score or 0.0
            if c.chunk_id not in seen or score > (seen[c.chunk_id].score or 0):
                seen[c.chunk_id] = SearchChunk(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    chunk_text=c.chunk_text,
                    source_url=c.source_url,
                    doc_type=c.doc_type,
                    score=score,
                    metadata=c.metadata,
                )
        return list(seen.values())

    def _merge_with_rrf(
        self,
        bm25_chunks: list[SearchChunk],
        vector_chunks: list[SearchChunk],
        k: int = 60,
    ) -> list[SearchChunk]:
        """Merge BM25 + vector results using Reciprocal Rank Fusion (RRF).
        RRF score = sum(1 / (k + rank)) for each list. Higher = better.
        """
        rrf_scores: dict[str, float] = {}
        chunk_by_id: dict[str, SearchChunk] = {}

        for rank, c in enumerate(bm25_chunks, start=1):
            rrf_scores[c.chunk_id] = rrf_scores.get(c.chunk_id, 0.0) + 1.0 / (k + rank)
            chunk_by_id[c.chunk_id] = c

        for rank, c in enumerate(vector_chunks, start=1):
            rrf_scores[c.chunk_id] = rrf_scores.get(c.chunk_id, 0.0) + 1.0 / (k + rank)
            chunk_by_id[c.chunk_id] = c

        # Sort by RRF score descending, return chunks with updated score for downstream
        sorted_ids = sorted(rrf_scores.keys(), key=lambda x: rrf_scores[x], reverse=True)
        result: list[SearchChunk] = []
        for chunk_id in sorted_ids:
            c = chunk_by_id[chunk_id]
            result.append(
                SearchChunk(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    chunk_text=c.chunk_text,
                    source_url=c.source_url,
                    doc_type=c.doc_type,
                    score=rrf_scores[chunk_id],
                    metadata=c.metadata,
                )
            )
        return result

    def _build_candidate_pool(
        self,
        merged: list[SearchChunk],
        bm25_ids: set[str],
        vector_ids: set[str],
        extra_ids: set[str],
        diversity_ids: set[str],
        stats: dict[str, Any],
        plan: RetrievalPlan | None,
    ) -> CandidatePool:
        """Build CandidatePool from merged chunks (Workstream 3)."""
        items: list[CandidateChunk] = []
        source_counts: dict[str, int] = {
            "bm25": 0,
            "vector": 0,
            "boosted_fetch": 0,
            "diversity_fetch": 0,
        }
        doc_type_counts: dict[str, int] = {}

        for c in merged:
            if c.chunk_id in diversity_ids:
                src = "diversity_fetch"
            elif c.chunk_id in extra_ids:
                src = "boosted_fetch"
            elif c.chunk_id in vector_ids and c.chunk_id not in bm25_ids:
                src = "vector"
            else:
                src = "bm25"
            source_counts[src] = source_counts.get(src, 0) + 1
            doc_type_counts[c.doc_type or "unknown"] = doc_type_counts.get(c.doc_type or "unknown", 0) + 1
            items.append(
                CandidateChunk(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    source_url=c.source_url or "",
                    doc_type=c.doc_type or "",
                    chunk_text=c.chunk_text,
                    retrieval_score=c.score,
                    retrieval_source=src,
                    metadata=c.metadata,
                )
            )
        return CandidatePool(
            items=items,
            source_counts=source_counts,
            doc_type_counts=doc_type_counts,
            retrieval_stats=stats,
            plan_used=plan,
        )

    @staticmethod
    def _split_primary_and_secondary_doc_types(
        doc_types: list[str] | None,
        preferred_sources: list[str] | None,
    ) -> tuple[list[str] | None, bool]:
        requested = [str(d).strip() for d in (doc_types or []) if str(d).strip()]
        wants_conversation = "conversation" in {
            str(s).strip().lower() for s in (preferred_sources or []) if str(s).strip()
        }
        if not requested:
            return None, wants_conversation
        non_conversation = [d for d in requested if d.lower() != "conversation"]
        if wants_conversation and non_conversation:
            return non_conversation, True
        return requested, wants_conversation or ("conversation" in {d.lower() for d in requested})

    @staticmethod
    def _dedupe_chunks(chunks: list[SearchChunk]) -> list[SearchChunk]:
        seen: dict[str, SearchChunk] = {}
        for chunk in chunks:
            if chunk.chunk_id not in seen or (chunk.score or 0.0) > (seen[chunk.chunk_id].score or 0.0):
                seen[chunk.chunk_id] = chunk
        return list(seen.values())

    @staticmethod
    def _score_calibration_factor(
        chunk: SearchChunk,
        *,
        page_kind_weights: dict[str, float] | None = None,
        product_family_weights: dict[str, float] | None = None,
        product_family_hints: list[str] | None = None,
        demote_doc_types: list[str] | None = None,
    ) -> float:
        factor = 1.0
        metadata = dict(chunk.metadata or {})
        page_kind = str(metadata.get("page_kind", "")).strip().lower()
        product_family = str(metadata.get("product_family", "")).strip().lower()
        doc_type = str(chunk.doc_type or "").strip().lower()

        if page_kind_weights and page_kind:
            try:
                factor *= max(0.1, float(page_kind_weights.get(page_kind, 1.0)))
            except (TypeError, ValueError):
                pass

        if product_family_weights and product_family:
            try:
                factor *= max(0.1, float(product_family_weights.get(product_family, 1.0)))
            except (TypeError, ValueError):
                pass
        elif product_family_hints:
            hints = {str(v).strip().lower() for v in product_family_hints if str(v).strip()}
            if hints and product_family and product_family not in hints:
                factor *= 0.92

        if demote_doc_types:
            demoted = {str(v).strip().lower() for v in demote_doc_types if str(v).strip()}
            if doc_type in demoted:
                factor *= 0.75

        return max(0.1, factor)

    @classmethod
    def _apply_search_calibration(
        cls,
        chunks: list[SearchChunk],
        *,
        page_kind_weights: dict[str, float] | None = None,
        product_family_weights: dict[str, float] | None = None,
        product_family_hints: list[str] | None = None,
        demote_doc_types: list[str] | None = None,
    ) -> list[SearchChunk]:
        calibrated: list[SearchChunk] = []
        for chunk in chunks:
            factor = cls._score_calibration_factor(
                chunk,
                page_kind_weights=page_kind_weights,
                product_family_weights=product_family_weights,
                product_family_hints=product_family_hints,
                demote_doc_types=demote_doc_types,
            )
            calibrated.append(
                SearchChunk(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    chunk_text=chunk.chunk_text,
                    source_url=chunk.source_url,
                    doc_type=chunk.doc_type,
                    score=(chunk.score or 0.0) * factor,
                    metadata=chunk.metadata,
                )
            )
        return calibrated

    @classmethod
    def _apply_rerank_calibration(
        cls,
        reranked: list[tuple[SearchChunk, float]],
        *,
        page_kind_weights: dict[str, float] | None = None,
        product_family_weights: dict[str, float] | None = None,
        product_family_hints: list[str] | None = None,
        demote_doc_types: list[str] | None = None,
    ) -> list[tuple[SearchChunk, float]]:
        calibrated: list[tuple[SearchChunk, float]] = []
        for chunk, score in reranked:
            factor = cls._score_calibration_factor(
                chunk,
                page_kind_weights=page_kind_weights,
                product_family_weights=product_family_weights,
                product_family_hints=product_family_hints,
                demote_doc_types=demote_doc_types,
            )
            calibrated.append((chunk, float(score or 0.0) * factor))
        return sorted(calibrated, key=lambda item: item[1], reverse=True)

    @staticmethod
    def _retain_supporting_conversation_chunk(
        selected: list[tuple[SearchChunk, float]],
        candidates: list[tuple[SearchChunk, float]],
        *,
        max_items: int,
    ) -> list[tuple[SearchChunk, float]]:
        if not candidates:
            return selected
        if any((chunk.doc_type or "").lower() == "conversation" for chunk, _ in selected):
            return selected
        conversation_candidate = next(
            ((chunk, score) for chunk, score in candidates if (chunk.doc_type or "").lower() == "conversation"),
            None,
        )
        if conversation_candidate is None:
            return selected
        updated = list(selected)
        updated.append(conversation_candidate)
        if len(updated) <= max_items:
            return updated
        non_conversation = [
            (idx, item) for idx, item in enumerate(updated)
            if (item[0].doc_type or "").lower() != "conversation"
        ]
        if not non_conversation:
            return updated[:max_items]
        remove_idx = min(non_conversation, key=lambda entry: entry[1][1])[0]
        return [item for idx, item in enumerate(updated) if idx != remove_idx][:max_items]

    async def _gather_chunk_tasks(
        self,
        tasks: dict[str, asyncio.Task[list[SearchChunk]]],
        *,
        stage: str,
    ) -> dict[str, list[SearchChunk]]:
        if not tasks:
            return {}
        names = list(tasks.keys())
        results = await asyncio.gather(*(tasks[name] for name in names), return_exceptions=True)
        out: dict[str, list[SearchChunk]] = {}
        for name, result in zip(names, results):
            if isinstance(result, Exception):
                logger.warning(
                    "retrieval_parallel_task_failed",
                    stage=stage,
                    task=name,
                    error=str(result),
                )
                out[name] = []
                continue
            out[name] = result
        return out

    async def _fetch_bm25_bundle(
        self,
        *,
        keyword_query: str,
        fetch_n: int,
        primary_doc_types: list[str] | None,
        authoritative_doc_types: list[str],
        supporting_doc_types: list[str],
        ensure_doc_types: list[str],
        diversity_doc_types: list[str],
        diversity_fetch_per_type: int,
        boost_pricing: bool,
        preferred_page_kinds: list[str] | None = None,
        product_family_hints: list[str] | None = None,
        page_kind_weights: dict[str, float] | None = None,
        product_family_weights: dict[str, float] | None = None,
    ) -> _Bm25Bundle:
        supporting_fetch_n = max(8, min(fetch_n // 2, 20))
        tasks: dict[str, asyncio.Task[list[SearchChunk]]] = {
            "primary": asyncio.create_task(
                self._search_opensearch_safe(
                    query=keyword_query,
                    top_n=fetch_n,
                    doc_types=authoritative_doc_types or primary_doc_types,
                    boost_pricing=boost_pricing,
                    page_kinds=preferred_page_kinds,
                    product_families=product_family_hints,
                    page_kind_weights=page_kind_weights,
                    product_family_weights=product_family_weights,
                )
            )
        }
        if ensure_doc_types:
            tasks["extra"] = asyncio.create_task(
                self._search_opensearch_safe(
                    query=keyword_query,
                    top_n=20,
                    doc_types=ensure_doc_types,
                    boost_pricing=True,
                    page_kinds=preferred_page_kinds,
                    product_families=product_family_hints,
                    page_kind_weights=page_kind_weights,
                    product_family_weights=product_family_weights,
                )
            )
        if supporting_doc_types and supporting_doc_types != authoritative_doc_types:
            tasks["supporting"] = asyncio.create_task(
                self._search_opensearch_safe(
                    query=keyword_query,
                    top_n=supporting_fetch_n,
                    doc_types=supporting_doc_types,
                    boost_pricing=False,
                    page_kinds=preferred_page_kinds,
                    product_families=product_family_hints,
                    page_kind_weights=page_kind_weights,
                    product_family_weights=product_family_weights,
                )
            )
        covered_doc_types = {
            d.strip().lower()
            for d in (authoritative_doc_types + supporting_doc_types + ensure_doc_types)
            if isinstance(d, str) and d.strip()
        }
        for doc_type in diversity_doc_types:
            dt = str(doc_type).strip().lower()
            if not dt or dt in covered_doc_types:
                continue
            tasks[f"diversity_{dt}"] = asyncio.create_task(
                self._search_opensearch_safe(
                    query=keyword_query,
                    top_n=diversity_fetch_per_type,
                    doc_types=[dt],
                    boost_pricing=False,
                    product_families=product_family_hints,
                    page_kind_weights=page_kind_weights,
                    product_family_weights=product_family_weights,
                )
            )

        results = await self._gather_chunk_tasks(tasks, stage="bm25")
        diversity_chunks: list[SearchChunk] = []
        for name, chunks in results.items():
            if name.startswith("diversity_"):
                diversity_chunks.extend(chunks or [])
        return _Bm25Bundle(
            primary=results.get("primary", []),
            extra=results.get("extra", []),
            supporting=results.get("supporting", []),
            diversity=diversity_chunks,
        )

    async def _fetch_vector_bundle(
        self,
        *,
        vector: list[float] | None,
        fetch_n: int,
        primary_doc_types: list[str] | None,
        authoritative_doc_types: list[str],
        supporting_doc_types: list[str],
        preferred_page_kinds: list[str] | None = None,
        product_family_hints: list[str] | None = None,
    ) -> _VectorBundle:
        if not vector:
            return _VectorBundle()

        supporting_fetch_n = max(8, min(fetch_n // 2, 20))
        tasks: dict[str, asyncio.Task[list[SearchChunk]]] = {
            "primary": asyncio.create_task(
                self._search_qdrant_safe(
                    vector=vector,
                    top_n=fetch_n,
                    doc_types=authoritative_doc_types or primary_doc_types,
                    page_kinds=preferred_page_kinds,
                    product_families=product_family_hints,
                )
            )
        }
        if supporting_doc_types and supporting_doc_types != authoritative_doc_types:
            tasks["supporting"] = asyncio.create_task(
                self._search_qdrant_safe(
                    vector=vector,
                    top_n=supporting_fetch_n,
                    doc_types=supporting_doc_types,
                    page_kinds=preferred_page_kinds,
                    product_families=product_family_hints,
                )
            )

        results = await self._gather_chunk_tasks(tasks, stage="vector")
        return _VectorBundle(
            primary=results.get("primary", []),
            supporting=results.get("supporting", []),
        )

    async def _fetch_parallel_candidates(
        self,
        *,
        qr: QueryRewrite,
        fetch_n: int,
        primary_doc_types: list[str] | None,
        authoritative_doc_types: list[str],
        supporting_doc_types: list[str],
        ensure_doc_types: list[str],
        diversity_doc_types: list[str],
        diversity_fetch_per_type: int,
        boost_pricing: bool,
        preferred_page_kinds: list[str] | None = None,
        product_family_hints: list[str] | None = None,
        page_kind_weights: dict[str, float] | None = None,
        product_family_weights: dict[str, float] | None = None,
    ) -> tuple[_Bm25Bundle, _VectorBundle]:
        bm25_bundle_task = asyncio.create_task(
            self._fetch_bm25_bundle(
                keyword_query=qr.keyword_query,
                fetch_n=fetch_n,
                primary_doc_types=primary_doc_types,
                authoritative_doc_types=authoritative_doc_types,
                supporting_doc_types=supporting_doc_types,
                ensure_doc_types=ensure_doc_types,
                diversity_doc_types=diversity_doc_types,
                diversity_fetch_per_type=diversity_fetch_per_type,
                boost_pricing=boost_pricing,
                preferred_page_kinds=preferred_page_kinds,
                product_family_hints=product_family_hints,
                page_kind_weights=page_kind_weights,
                product_family_weights=product_family_weights,
            )
        )
        embed_task = asyncio.create_task(self._embed_query_safe(qr.semantic_query))

        vector: list[float] | None = None
        try:
            vector = await embed_task
        except Exception as exc:
            logger.warning("retrieval_embedding_task_failed", error=str(exc))

        vector_bundle_task: asyncio.Task[_VectorBundle] | None = None
        if vector:
            vector_bundle_task = asyncio.create_task(
                self._fetch_vector_bundle(
                    vector=vector,
                    fetch_n=fetch_n,
                    primary_doc_types=primary_doc_types,
                    authoritative_doc_types=authoritative_doc_types,
                    supporting_doc_types=supporting_doc_types,
                    preferred_page_kinds=preferred_page_kinds,
                    product_family_hints=product_family_hints,
                )
            )

        bm25_bundle = _Bm25Bundle()
        try:
            bm25_bundle = await bm25_bundle_task
        except Exception as exc:
            logger.warning("retrieval_bm25_bundle_failed", error=str(exc))

        vector_bundle = _VectorBundle()
        if vector_bundle_task is not None:
            try:
                vector_bundle = await vector_bundle_task
            except Exception as exc:
                logger.warning("retrieval_vector_bundle_failed", error=str(exc))

        return bm25_bundle, vector_bundle

    async def retrieve(
        self,
        query: str,
        top_n: int | None = None,
        top_k: int | None = None,
        doc_types: list[str] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        retry_strategy: RetryStrategy | None = None,
        attempt: int = 1,
        query_spec: QuerySpec | None = None,
        retrieval_plan: RetrievalPlan | None = None,
    ) -> EvidencePack:
        """Execute hybrid retrieval pipeline. Workstream 3: plan → CandidatePool → EvidenceSet."""
        effective_query = (
            retry_strategy.suggested_query if retry_strategy and retry_strategy.suggested_query else query
        )
        effective_query_spec = query_spec
        requested_doc_types = doc_types

        planning_debug: dict[str, Any] = {}
        if retrieval_plan is None:
            plan, planning_debug = await build_retrieval_plan_for_attempt(
                base_query=effective_query,
                attempt=attempt,
                query_spec=effective_query_spec,
                retry_strategy=retry_strategy,
                explicit_override=None,
                conversation_history=conversation_history,
            )
        else:
            plan = retrieval_plan
            planning_debug = {
                "selected_retrieval_query": plan.query_semantic or plan.query_keyword,
                "query_source": "provided_plan",
                "rewrite_candidates": list(plan.fallback_queries or []),
            }
        qr = QueryRewrite(
            keyword_query=plan.query_keyword or effective_query,
            semantic_query=plan.query_semantic or effective_query,
            retrieval_profile=plan.profile,
        )
        try:
            from app.services.flow_debug import _pipeline_log
            _pipeline_log(
                "retrieval", "query_rewrite",
                keyword_query=qr.keyword_query[:100],
                semantic_query=qr.semantic_query[:100],
                profile=plan.profile,
                attempt=attempt,
            )
        except Exception:
            pass
        # Retrieval plan is authoritative for profile/doc type strategy.
        profile = plan.profile
        effective_doc_types = requested_doc_types or plan.preferred_doc_types
        if retry_strategy and retry_strategy.filter_doc_types:
            effective_doc_types = retry_strategy.filter_doc_types
        preferred_sources = list(plan.preferred_sources or [])
        primary_doc_types, include_conversation_source = self._split_primary_and_secondary_doc_types(
            effective_doc_types,
            preferred_sources,
        )
        authoritative_doc_types = [
            str(x).strip() for x in (plan.authoritative_doc_types or primary_doc_types or []) if str(x).strip()
        ]
        supporting_doc_types = [
            str(x).strip() for x in (plan.supporting_doc_types or []) if str(x).strip()
        ]
        if include_conversation_source and "conversation" not in supporting_doc_types:
            supporting_doc_types.append("conversation")

        fetch_n = plan.fetch_n or top_n or self._settings.retrieval_top_n
        rerank_k = plan.rerank_k or top_k or self._settings.retrieval_top_k
        plan_hint = dict(getattr(plan, "budget_hint", None) or {})
        hard_requirements = {
            str(x)
            for x in (plan_hint.get("hard_requirements") or [])
            if isinstance(x, str)
        }
        try:
            from app.services.archi_config import get_page_kind_filter_enabled
            page_kind_filter_enabled = get_page_kind_filter_enabled()
        except Exception:
            page_kind_filter_enabled = getattr(self._settings, "page_kind_filter_enabled", False)
        page_kind_weighting_enabled = bool(
            getattr(self._settings, "retrieval_page_kind_weighting_enabled", True)
        ) and bool(page_kind_filter_enabled)
        preferred_page_kinds = [
            str(x).strip().lower()
            for x in (plan_hint.get("preferred_page_kinds") or [])
            if str(x).strip()
        ]
        supporting_page_kinds = [
            str(x).strip().lower()
            for x in (plan_hint.get("supporting_page_kinds") or [])
            if str(x).strip()
        ]
        page_kind_weights: dict[str, float] = {}
        if isinstance(plan_hint.get("page_kind_weights"), dict):
            for k, v in (plan_hint.get("page_kind_weights") or {}).items():
                key = str(k).strip().lower()
                if not key:
                    continue
                try:
                    page_kind_weights[key] = float(v)
                except (TypeError, ValueError):
                    continue
        product_family_hints = [
            str(x).strip().lower()
            for x in (plan_hint.get("product_family_hints") or [])
            if str(x).strip()
        ]
        product_family_weights: dict[str, float] = {}
        if isinstance(plan_hint.get("product_family_weights"), dict):
            for k, v in (plan_hint.get("product_family_weights") or {}).items():
                key = str(k).strip().lower()
                if not key:
                    continue
                try:
                    product_family_weights[key] = float(v)
                except (TypeError, ValueError):
                    continue
        demote_doc_types = [
            str(x).strip().lower()
            for x in (plan_hint.get("demote_doc_types") or [])
            if str(x).strip()
        ]
        if not page_kind_weighting_enabled:
            preferred_page_kinds = []
            supporting_page_kinds = []
            page_kind_weights = {}
            demote_doc_types = []
        is_pricing_retrieval = bool(plan_hint.get("boost_pricing", False))
        ensure_doc_types = [
            str(x).strip()
            for x in (plan_hint.get("ensure_doc_types") or [])
            if str(x).strip()
        ]
        diversity_doc_types = [
            str(x).strip()
            for x in (plan_hint.get("diversity_doc_types") or [])
            if str(x).strip()
        ]
        try:
            diversity_fetch_per_type = max(
                1,
                int(
                    plan_hint.get("diversity_fetch_per_type")
                    or getattr(self._settings, "retrieval_diversity_fetch_per_type", 6)
                ),
            )
        except Exception:
            diversity_fetch_per_type = 6

        bm25_bundle, vector_bundle = await self._fetch_parallel_candidates(
            qr=qr,
            fetch_n=fetch_n,
            primary_doc_types=primary_doc_types,
            authoritative_doc_types=authoritative_doc_types,
            supporting_doc_types=supporting_doc_types,
            ensure_doc_types=ensure_doc_types,
            diversity_doc_types=diversity_doc_types,
            diversity_fetch_per_type=diversity_fetch_per_type,
            boost_pricing=is_pricing_retrieval or bool(retry_strategy and retry_strategy.boost_patterns),
            preferred_page_kinds=(preferred_page_kinds + supporting_page_kinds) or None,
            product_family_hints=product_family_hints or None,
            page_kind_weights=page_kind_weights or None,
            product_family_weights=product_family_weights or None,
        )
        bm25_chunks = bm25_bundle.primary
        extra_bm25 = bm25_bundle.extra
        supporting_bm25 = bm25_bundle.supporting
        diversity_bm25 = bm25_bundle.diversity
        vector_chunks = vector_bundle.primary
        supporting_vector = vector_bundle.supporting

        bm25_chunks = self._apply_search_calibration(
            bm25_chunks,
            page_kind_weights=page_kind_weights or None,
            product_family_weights=product_family_weights or None,
            product_family_hints=product_family_hints or None,
            demote_doc_types=demote_doc_types or None,
        )
        supporting_bm25 = self._apply_search_calibration(
            supporting_bm25,
            page_kind_weights=page_kind_weights or None,
            product_family_weights=product_family_weights or None,
            product_family_hints=product_family_hints or None,
            demote_doc_types=demote_doc_types or None,
        )
        diversity_bm25 = self._apply_search_calibration(
            diversity_bm25,
            page_kind_weights=page_kind_weights or None,
            product_family_weights=product_family_weights or None,
            product_family_hints=product_family_hints or None,
            demote_doc_types=demote_doc_types or None,
        )
        vector_chunks = self._apply_search_calibration(
            vector_chunks,
            page_kind_weights=page_kind_weights or None,
            product_family_weights=product_family_weights or None,
            product_family_hints=product_family_hints or None,
            demote_doc_types=demote_doc_types or None,
        )
        supporting_vector = self._apply_search_calibration(
            supporting_vector,
            page_kind_weights=page_kind_weights or None,
            product_family_weights=product_family_weights or None,
            product_family_hints=product_family_hints or None,
            demote_doc_types=demote_doc_types or None,
        )

        bm25_ids = {c.chunk_id for c in bm25_chunks}
        vector_ids = {c.chunk_id for c in vector_chunks}
        extra_ids = {c.chunk_id for c in extra_bm25}
        diversity_ids = {c.chunk_id for c in diversity_bm25}
        if supporting_doc_types and supporting_doc_types != authoritative_doc_types:
            extra_ids.update(c.chunk_id for c in supporting_bm25 + supporting_vector)

        if self._settings.retrieval_fusion == "rrf":
            merged = self._merge_with_rrf(
                self._dedupe_chunks(bm25_chunks + supporting_bm25),
                self._dedupe_chunks(vector_chunks + supporting_vector),
                k=self._settings.retrieval_rrf_k,
            )
        else:
            merged = self._merge_simple(
                self._dedupe_chunks(bm25_chunks + supporting_bm25),
                self._dedupe_chunks(vector_chunks + supporting_vector),
            )

        if extra_bm25 or diversity_bm25:
            seen_ids = {c.chunk_id for c in merged}
            scores = [c.score for c in merged] if merged else [0.0]
            median_score = sorted(scores)[len(scores) // 2] if scores else 0.01
            for c in extra_bm25:
                if c.chunk_id not in seen_ids:
                    seen_ids.add(c.chunk_id)
                    merged.append(
                        SearchChunk(
                            chunk_id=c.chunk_id,
                            document_id=c.document_id,
                            chunk_text=c.chunk_text,
                            source_url=c.source_url,
                            doc_type=c.doc_type,
                            score=max(c.score, median_score * 0.9),
                            metadata=c.metadata,
                        )
                    )
            for c in diversity_bm25:
                if c.chunk_id not in seen_ids:
                    seen_ids.add(c.chunk_id)
                    merged.append(
                        SearchChunk(
                            chunk_id=c.chunk_id,
                            document_id=c.document_id,
                            chunk_text=c.chunk_text,
                            source_url=c.source_url,
                            doc_type=c.doc_type,
                            score=max(c.score, median_score * 0.85),
                            metadata=c.metadata,
                        )
                    )

        stats: dict[str, Any] = {
            "bm25_count": len(bm25_chunks),
            "vector_count": len(vector_chunks),
            "supporting_bm25_count": len(supporting_bm25),
            "supporting_vector_count": len(supporting_vector),
            "diversity_bm25_count": len(diversity_bm25),
            "merged_count": len(merged),
            "fusion": self._settings.retrieval_fusion,
            "retrieval_profile": profile,
            "active_hypothesis": plan.active_hypothesis_name,
            "evidence_families": list(plan.evidence_families or []),
            "query_rewrite": {"keyword_query": qr.keyword_query, "semantic_query": qr.semantic_query},
            "attempt": attempt,
            "plan_reason": plan.reason,
            "plan_budget_hint": plan_hint,
            "query_source": planning_debug.get("query_source"),
        }
        if hard_requirements:
            stats["hard_requirements"] = sorted(hard_requirements)
        if primary_doc_types:
            stats["primary_doc_types"] = primary_doc_types
        if authoritative_doc_types:
            stats["authoritative_doc_types"] = authoritative_doc_types
        if supporting_doc_types:
            stats["supporting_doc_types"] = supporting_doc_types
        if preferred_page_kinds:
            stats["preferred_page_kinds"] = preferred_page_kinds
        if supporting_page_kinds:
            stats["supporting_page_kinds"] = supporting_page_kinds
        if product_family_hints:
            stats["product_family_hints"] = product_family_hints
        if demote_doc_types:
            stats["demote_doc_types"] = demote_doc_types
        if preferred_sources:
            stats["preferred_sources"] = preferred_sources
        if effective_query_spec and getattr(effective_query_spec, "rewrite_candidates", None):
            stats["rewrite_candidates"] = effective_query_spec.rewrite_candidates[:3]
        if retry_strategy:
            stats["retry_strategy"] = {
                "boost_patterns": (retry_strategy.boost_patterns or [])[:5],
                "filter_doc_types": retry_strategy.filter_doc_types,
                "context_expansion": retry_strategy.context_expansion,
            }
        if ensure_doc_types:
            stats["intent_fetch_doc_types"] = ensure_doc_types
            stats["intent_fetch_count"] = len(extra_bm25)
        if diversity_doc_types:
            stats["diversity_doc_types"] = diversity_doc_types
            doc_type_seen = {
                (c.doc_type or "").strip().lower()
                for c in (bm25_chunks + vector_chunks + supporting_bm25 + supporting_vector + diversity_bm25)
                if (c.doc_type or "").strip()
            }
            stats["diversity_doc_types_covered"] = [d for d in diversity_doc_types if d.lower() in doc_type_seen]

        if not merged:
            try:
                from app.core.metrics import retrieval_requests_total, retrieval_miss_rate
                retrieval_requests_total.inc()
                retrieval_miss_rate.inc()
            except Exception:
                pass
            return EvidencePack(
                chunks=[],
                retrieval_stats=stats,
                retrieval_plan=plan,
                candidate_pool=self._build_candidate_pool(
                    [], set(), set(), set(), set(), stats, plan
                ),
                evidence_set=None,
            )

        rerank_k = min(rerank_k, len(merged))
        reranked = await self._reranker.rerank(effective_query, merged, rerank_k)

        candidate_pool = self._build_candidate_pool(
            merged, bm25_ids, vector_ids, extra_ids, diversity_ids, stats, plan
        )

        reranked_search: list[tuple[SearchChunk, float]] = list(reranked)
        reranked_search = self._apply_rerank_calibration(
            reranked_search,
            page_kind_weights=page_kind_weights or None,
            product_family_weights=product_family_weights or None,
            product_family_hints=product_family_hints or None,
            demote_doc_types=demote_doc_types or None,
        )
        # Deprioritize conversation chunks: prefer docs; use conversation when docs lack ideal chunks
        penalty = getattr(self._settings, "retrieval_conversation_score_penalty", 1.0)
        if penalty < 1.0:
            reranked_search = [
                (c, (s * penalty) if (c.doc_type or "").lower() == "conversation" else s)
                for c, s in reranked_search
            ]
            reranked_search = sorted(reranked_search, key=lambda x: x[1], reverse=True)
        if retry_strategy and retry_strategy.exclude_patterns:
            exclude_re = re.compile(
                "|".join(re.escape(p) for p in retry_strategy.exclude_patterns),
                re.I,
            )
            before = len(reranked_search)
            reranked_search = [
                (c, s) for c, s in reranked_search
                if not exclude_re.search((c.chunk_text or "")[:500])
                and not exclude_re.search(c.source_url or "")
            ]
            if before > len(reranked_search):
                stats["exclude_patterns_filtered"] = before - len(reranked_search)

        # Evidence Selector: coverage-aware selection (Phase 1)
        required_evidence = list(plan.active_required_evidence or [])
        if not required_evidence and effective_query_spec:
            required_evidence = list(
                getattr(effective_query_spec, "hard_requirements", None)
                or effective_query_spec.required_evidence
                or []
            )
        coverage_map: dict[str, str] | None = None
        selector_candidates = list(reranked_search)
        if self._settings.evidence_selector_use_llm and reranked_search:
            from app.services.evidence_selector import select_evidence_for_query
            product_type = None
            if effective_query_spec and getattr(effective_query_spec, "resolved_slots", None):
                product_type = str((effective_query_spec.resolved_slots or {}).get("product_type", "")).strip() or None
            selection = await select_evidence_for_query(
                effective_query,
                reranked_search,
                required_evidence=required_evidence if required_evidence else None,
                product_type=product_type,
                top_k_fallback=self._settings.evidence_selector_fallback_top_k,
            )
            reranked_search = selection.selected
            if selection.used_llm:
                coverage_map = selection.coverage_map
                stats["evidence_selector"] = {
                    "used_llm": True,
                    "coverage_map": selection.coverage_map,
                    "uncovered_requirements": selection.uncovered_requirements[:5],
                    "reasoning": selection.reasoning[:100],
                }
        reranked_search = self._retain_supporting_conversation_chunk(
            reranked_search,
            selector_candidates,
            max_items=max(rerank_k, self._settings.evidence_selector_fallback_top_k),
        )

        evidence_set = build_evidence_set(
            reranked_search, effective_query_spec, plan, candidate_pool,
            coverage_map=coverage_map,
        )
        evidence = list(evidence_set.chunks)

        min_ensure = self._settings.retrieval_ensure_doc_type_min
        # Policy/troubleshooting profiles: ensure stronger doc_type representation
        if ensure_doc_types and set(ensure_doc_types) & {"policy", "tos"}:
            min_ensure = max(min_ensure, 3)
        elif ensure_doc_types and set(ensure_doc_types) & {"howto", "docs", "faq"}:
            min_ensure = max(min_ensure, 2)
        if min_ensure > 0 and ensure_doc_types:
            ensure_set = set(ensure_doc_types)
            count_ensure = sum(1 for e in evidence if e.doc_type in ensure_set)
            if count_ensure < min_ensure:
                need = min_ensure - count_ensure
                evidence_ids = {e.chunk_id for e in evidence}
                candidates = [
                    c for c in merged
                    if c.chunk_id not in evidence_ids and c.doc_type in ensure_set
                ]
                for c in candidates[:need]:
                    evidence.append(
                        EvidenceChunk(
                            chunk_id=c.chunk_id,
                            snippet=(c.chunk_text or "")[:500] + ("..." if len(c.chunk_text or "") > 500 else ""),
                            source_url=c.source_url or "",
                            doc_type=c.doc_type or "",
                            score=c.score,
                            full_text=c.chunk_text,
                        )
                    )
                if len(evidence) > rerank_k:
                    by_score = sorted(
                        [e for e in evidence if e.doc_type not in ensure_set],
                        key=lambda e: e.score or 0,
                    )
                    to_remove = min(len(evidence) - rerank_k, len(by_score))
                    remove_ids = {by_score[i].chunk_id for i in range(to_remove)}
                    evidence = [e for e in evidence if e.chunk_id not in remove_ids]
                chunk_by_id = {c.chunk_id: c for c in merged}
                reranked_rebuild = [
                    (chunk_by_id[e.chunk_id], e.score or 0)
                    for e in evidence
                    if e.chunk_id in chunk_by_id
                ]
                evidence_set = build_evidence_set(
                    reranked_rebuild, effective_query_spec, plan, candidate_pool,
                    coverage_map=None,  # rebuild: use heuristic (chunks changed)
                )
                evidence = list(evidence_set.chunks)

        stats["reranked_count"] = len(evidence)
        try:
            from app.core.metrics import (
                retrieval_requests_total,
                retrieval_chunks_returned,
                retrieval_hit_rate,
            )
            retrieval_requests_total.inc()
            retrieval_chunks_returned.observe(len(evidence))
            retrieval_hit_rate.inc()
        except Exception:
            pass
        return EvidencePack(
            chunks=evidence,
            retrieval_stats=stats,
            retrieval_plan=plan,
            candidate_pool=candidate_pool,
            evidence_set=evidence_set,
        )
