"""Hybrid retrieval: BM25 + vector + rerank."""

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

from app.services.retry_planner import RetryStrategy
from app.services.schemas import QuerySpec

logger = get_logger(__name__)


@dataclass
class EvidencePack:
    """Retrieved evidence for answer generation."""

    chunks: list[EvidenceChunk] = field(default_factory=list)
    retrieval_stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class QueryRewrite:
    """Query rewrite for dual retrieval."""

    keyword_query: str
    semantic_query: str


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

    def _rewrite_with_conversation(
        self, query: str, conversation_history: list[dict[str, str]] | None
    ) -> str:
        """Rewrite query using conversation context for better retrieval."""
        if not conversation_history or len(conversation_history) < 2:
            return query
        # Build context from last exchange: extract key terms (exclude stopwords)
        # e.g. User: "VPS plans?" -> Assistant: "We have Pro, Basic..." -> User: "Price?"
        # -> "VPS plans Pro Basic price pricing"
        _STOPWORDS = {"hello", "hi", "hey", "chào", "xin", "thanks", "thank", "ok", "okay"}
        context_terms: list[str] = []
        for m in conversation_history[-4:]:
            content = (m.get("content") or "").strip()
            if not content or len(content) > 200:
                continue
            if m.get("role") == "user" and len(context_terms) < 3:
                words = [
                    w for w in content.split()
                    if len(w) > 2 and w.lower() not in _STOPWORDS
                ][:5]
                context_terms.extend(words)
        if context_terms:
            # Dedupe and combine with current query
            seen = set()
            unique = []
            for t in context_terms:
                tl = t.lower()
                if tl not in seen and tl not in query.lower():
                    seen.add(tl)
                    unique.append(t)
            if unique:
                return f"{' '.join(unique[:3])} {query}".strip()
        return query

    def _query_rewrite(
        self,
        query: str,
        conversation_history: list[dict[str, str]] | None = None,
        retry_strategy: RetryStrategy | None = None,
        query_spec: QuerySpec | None = None,
    ) -> QueryRewrite:
        """Rewrite query: use QuerySpec when available, else conversation context + expand plans/pricing."""
        if query_spec and query_spec.keyword_queries and query_spec.semantic_queries:
            return QueryRewrite(
                keyword_query=query_spec.keyword_queries[0],
                semantic_query=query_spec.semantic_queries[0],
            )
        # Fallback: conversation-aware
        semantic_query = self._rewrite_with_conversation(query, conversation_history)
        q = semantic_query.lower()
        keyword_query = semantic_query
        # Expand "VPS plans" type queries for better BM25 hits on pricing docs
        if any(kw in q for kw in ["plan", "plans", "price", "pricing", "vps", "offer", "cost", "link"]):
            extras = []
            if "plan" in q or "plans" in q or "link" in q:
                extras.extend(["pricing", "budget", "windows vps", "kvm vps", "storage", "order", "store"])
            if "price" in q or "cost" in q:
                extras.extend(["USD", "monthly", "annually", "pricing"])
            if "refund" in q or "return" in q:
                extras.extend(["policy", "terms", "30 days"])
            if "support" in q or "help" in q:
                extras.extend(["contact", "email", "FAQ"])
            if extras:
                keyword_query = f"{semantic_query} {' '.join(extras[:4])}"
        # Retry attempt 2: append boost patterns from Retry Planner
        if retry_strategy and retry_strategy.boost_patterns:
            boost = " ".join(p for p in retry_strategy.boost_patterns if not p.startswith("\\"))
            if boost:
                keyword_query = f"{keyword_query} {boost}"
        return QueryRewrite(keyword_query=keyword_query, semantic_query=semantic_query)

    def _merge_simple(
        self,
        bm25_chunks: list[SearchChunk],
        vector_chunks: list[SearchChunk],
    ) -> list[SearchChunk]:
        """Merge and dedupe by chunk_id. Prefer higher score when duplicate."""
        seen: dict[str, SearchChunk] = {}
        for c in bm25_chunks + vector_chunks:
            if c.chunk_id not in seen or c.score > seen[c.chunk_id].score:
                seen[c.chunk_id] = c
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
    ) -> EvidencePack:
        """Execute hybrid retrieval pipeline.

        Attempt 1: broad hybrid (retry_strategy=None).
        Attempt 2: precision targeted by retry_strategy from Retry Planner.
        """
        top_n = top_n or self._settings.retrieval_top_n
        top_k = top_k or self._settings.retrieval_top_k

        # Attempt 2: use suggested_query from Evidence Evaluator when present
        effective_query = (
            retry_strategy.suggested_query if retry_strategy and retry_strategy.suggested_query else query
        )
        # When using suggested_query, skip query_spec (it was built from original query)
        effective_query_spec = None if (retry_strategy and retry_strategy.suggested_query) else query_spec

        qr = self._query_rewrite(effective_query, conversation_history, retry_strategy, effective_query_spec)

        # Attempt 2: use filter_doc_types from retry strategy when present
        effective_doc_types = doc_types
        if retry_strategy and retry_strategy.filter_doc_types:
            effective_doc_types = retry_strategy.filter_doc_types

        # For plans/pricing queries: fetch more and don't filter by doc_type
        q_lower = effective_query.lower()
        is_plans_query = any(kw in q_lower for kw in ["plan", "plans", "price", "pricing", "vps", "offer", "link"])
        fetch_n = min(top_n * 2, 100) if is_plans_query else top_n

        # 1. BM25 from OpenSearch (boost pricing chunks for plan/price queries)
        bm25_chunks = await self._opensearch.search(
            query=qr.keyword_query,
            top_n=fetch_n,
            doc_types=effective_doc_types,
            boost_pricing=is_plans_query or bool(retry_strategy and retry_strategy.boost_patterns),
        )

        # 1b. Intent-aware fetch: for plans/price queries, also fetch from configured doc_types
        # (ensures pricing chunks are in the pool even if BM25 ranks nav chunks higher)
        ensure_doc_types: list[str] = []
        if is_plans_query and self._settings.retrieval_plans_fetch_doc_types:
            ensure_doc_types = [
                t.strip() for t in self._settings.retrieval_plans_fetch_doc_types.split(",") if t.strip()
            ]
        extra_bm25: list[SearchChunk] = []
        if ensure_doc_types:
            extra_bm25 = await self._opensearch.search(
                query=qr.keyword_query,
                top_n=20,
                doc_types=ensure_doc_types,
                boost_pricing=True,
            )

        # 2. Vector from Qdrant (sync client - run in thread)
        vectors = await self._embedder.embed([qr.semantic_query])
        vector_chunks = await asyncio.to_thread(
            self._qdrant.search,
            vector=vectors[0],
            top_n=fetch_n,
            doc_types=effective_doc_types,
        )

        # 3. Merge: RRF (strong) or simple dedupe
        if self._settings.retrieval_fusion == "rrf":
            merged = self._merge_with_rrf(
                bm25_chunks, vector_chunks, k=self._settings.retrieval_rrf_k
            )
        else:
            merged = self._merge_simple(bm25_chunks, vector_chunks)

        # 3b. Inject intent-fetched chunks (pricing etc.) into pool so reranker can consider them
        if extra_bm25:
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

        stats = {
            "bm25_count": len(bm25_chunks),
            "vector_count": len(vector_chunks),
            "merged_count": len(merged),
            "fusion": self._settings.retrieval_fusion,
            "query_rewrite": {
                "keyword_query": qr.keyword_query,
                "semantic_query": qr.semantic_query,
            },
            "attempt": attempt,
        }
        if retry_strategy:
            stats["retry_strategy"] = {
                "boost_patterns": retry_strategy.boost_patterns[:5],
                "filter_doc_types": retry_strategy.filter_doc_types,
                "context_expansion": retry_strategy.context_expansion,
            }
        if ensure_doc_types:
            stats["intent_fetch_doc_types"] = ensure_doc_types
            stats["intent_fetch_count"] = len(extra_bm25)

        if not merged:
            try:
                from app.core.metrics import retrieval_requests_total, retrieval_miss_rate
                retrieval_requests_total.inc()
                retrieval_miss_rate.inc()
            except Exception:
                pass
            stats["query_rewrite"] = {"keyword_query": qr.keyword_query, "semantic_query": qr.semantic_query}
            return EvidencePack(chunks=[], retrieval_stats=stats)

        # 4. Rerank - use more chunks for plans/pricing queries
        extra = self._settings.retrieval_plans_extra_chunks
        rerank_k = min(top_k + extra, len(merged)) if is_plans_query else top_k
        reranked = await self._reranker.rerank(effective_query, merged, rerank_k)

        evidence = [
            EvidenceChunk(
                chunk_id=c.chunk_id,
                snippet=c.chunk_text[:500] + ("..." if len(c.chunk_text) > 500 else ""),
                source_url=c.source_url,
                doc_type=c.doc_type,
                score=score,
                full_text=c.chunk_text,
            )
            for c, score in reranked
        ]

        # 4a. Retry Attempt 2: exclude chunks matching exclude_patterns (boilerplate)
        if retry_strategy and retry_strategy.exclude_patterns:
            exclude_re = re.compile(
                "|".join(re.escape(p) for p in retry_strategy.exclude_patterns),
                re.I,
            )
            before = len(evidence)
            evidence = [
                e for e in evidence
                if not exclude_re.search((e.full_text or e.snippet) or "")
                and not exclude_re.search(e.source_url or "")
            ]
            if before > len(evidence):
                stats["exclude_patterns_filtered"] = before - len(evidence)

        # 4b. Diversity: ensure minimum chunks from ensure_doc_types (e.g. pricing)
        min_ensure = self._settings.retrieval_ensure_doc_type_min
        if min_ensure > 0 and ensure_doc_types:
            ensure_set = set(ensure_doc_types)
            count_ensure = sum(1 for e in evidence if e.doc_type in ensure_set)
            if count_ensure < min_ensure:
                need = min_ensure - count_ensure
                # Get top pricing chunks from merged not yet in evidence
                evidence_ids = {e.chunk_id for e in evidence}
                candidates = [
                    c for c in merged
                    if c.chunk_id not in evidence_ids and c.doc_type in ensure_set
                ]
                for c in candidates[:need]:
                    evidence.append(
                        EvidenceChunk(
                            chunk_id=c.chunk_id,
                            snippet=c.chunk_text[:500] + ("..." if len(c.chunk_text) > 500 else ""),
                            source_url=c.source_url,
                            doc_type=c.doc_type,
                            score=c.score,
                            full_text=c.chunk_text,
                        )
                    )
                # Replace lowest-scoring non-ensure chunks to keep total ~rerank_k
                if len(evidence) > rerank_k:
                    by_score = sorted(
                        [e for e in evidence if e.doc_type not in ensure_set],
                        key=lambda e: e.score or 0,
                    )
                    to_remove = min(len(evidence) - rerank_k, len(by_score))
                    remove_ids = {by_score[i].chunk_id for i in range(to_remove)}
                    evidence = [e for e in evidence if e.chunk_id not in remove_ids]

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
        return EvidencePack(chunks=evidence, retrieval_stats=stats)
