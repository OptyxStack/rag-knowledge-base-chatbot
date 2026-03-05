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
from app.services.retrieval_planner import build_retrieval_plan
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

        _STOPWORDS = {"hello", "hi", "hey", "thanks", "thank", "ok", "okay"}
        context_terms: list[str] = []
        for m in conversation_history[-4:]:
            content = (m.get("content") or "").strip()
            if not content or len(content) > 200:
                continue
            if m.get("role") == "user":
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
                return f"{' '.join(unique)} {query}".strip()
        return query

    def _resolve_retrieval_profile(
        self,
        query: str,
        query_spec: QuerySpec | None,
    ) -> str:
        """Resolve the active retrieval profile.

        QuerySpec is the preferred source of truth. Fallback heuristics exist only
        for backward compatibility when QuerySpec is absent or minimal.
        """
        if query_spec and getattr(query_spec, "retrieval_profile", None):
            return query_spec.retrieval_profile

        q = query.lower()
        if any(kw in q for kw in ["price", "cost", "pricing", "order", "buy", "subscribe", "link"]):
            return "pricing_profile"
        if any(kw in q for kw in ["refund", "policy", "terms", "cancellation"]):
            return "policy_profile"
        if any(kw in q for kw in ["how", "setup", "install", "fix", "error", "step"]):
            return "troubleshooting_profile"
        if any(kw in q for kw in ["compare", "diff", "difference", "vs", "versus"]):
            return "comparison_profile"
        if any(kw in q for kw in ["account", "login", "billing"]):
            return "account_profile"
        return "generic_profile"

    def _resolve_hard_requirements(self, query_spec: QuerySpec | None) -> set[str]:
        """Return hard requirements, with required_evidence as compatibility fallback."""
        if not query_spec:
            return set()
        hard = getattr(query_spec, "hard_requirements", None) or query_spec.required_evidence or []
        return {str(x) for x in hard if isinstance(x, str)}

    def _is_pricing_retrieval(
        self,
        query: str,
        profile: str,
        query_spec: QuerySpec | None,
        hard_requirements: set[str],
    ) -> bool:
        """Decide whether pricing-oriented retrieval heuristics should apply."""
        q_lower = query.lower()
        if profile == "pricing_profile":
            return True
        if profile == "comparison_profile" and any(
            kw in q_lower for kw in ["price", "pricing", "cost", "plan", "offer", "link"]
        ):
            return True
        if hard_requirements & {"transaction_link"}:
            return True
        # Backward compatibility: keep the old broad heuristic only when QuerySpec
        # is absent, so newer QuerySpec-driven requests are less noisy.
        legacy_keywords = ["plan", "plans", "price", "pricing", "vps", "offer", "link"]
        if query_spec is not None:
            legacy_keywords = ["plan", "plans", "price", "pricing", "offer", "cost", "link"]
        return any(kw in q_lower for kw in legacy_keywords)

    def _derive_ensure_doc_types(
        self,
        profile: str,
        query_spec: QuerySpec | None,
        is_pricing_retrieval: bool,
    ) -> list[str]:
        """Derive doc types that should be explicitly represented in the final evidence."""
        ensure_doc_types: list[str] = []
        hard_requirements = self._resolve_hard_requirements(query_spec)
        soft_requirements = {
            str(x)
            for x in (getattr(query_spec, "soft_requirements", None) or [])
            if isinstance(x, str)
        }

        if is_pricing_retrieval and self._settings.retrieval_plans_fetch_doc_types:
            ensure_doc_types.extend(
                t.strip()
                for t in self._settings.retrieval_plans_fetch_doc_types.split(",")
                if t.strip()
            )

        if profile == "policy_profile" or "policy_language" in hard_requirements:
            policy_types = [
                t.strip()
                for t in (self._settings.retrieval_policy_doc_types or "").split(",")
                if t.strip()
            ]
            if policy_types:
                ensure_doc_types.extend(policy_types)
                ensure_doc_types.append("pricing")  # product-specific policy disclaimers

        if profile == "troubleshooting_profile" or "steps_structure" in hard_requirements:
            ensure_doc_types.extend(["howto", "docs", "faq"])

        if "has_any_url" in hard_requirements or "has_any_url" in soft_requirements:
            ensure_doc_types.append("faq")

        return list(dict.fromkeys(ensure_doc_types))

    async def _query_rewrite(
        self,
        query: str,
        conversation_history: list[dict[str, str]] | None = None,
        retry_strategy: RetryStrategy | None = None,
        query_spec: QuerySpec | None = None,
    ) -> QueryRewrite:
        """Rewrite query: use QuerySpec when available, else LLM rewriter or heuristic."""
        if query_spec:
            keyword_query = query_spec.keyword_queries[0] if query_spec.keyword_queries else ""
            semantic_query = query_spec.semantic_queries[0] if query_spec.semantic_queries else ""

            if not keyword_query and getattr(query_spec, "rewrite_candidates", None):
                keyword_query = query_spec.rewrite_candidates[0]
            if not semantic_query and getattr(query_spec, "rewrite_candidates", None):
                semantic_query = query_spec.rewrite_candidates[0]

            if retry_strategy and not retry_strategy.suggested_query and query_spec.rewrite_candidates:
                alternate = next(
                    (
                        candidate
                        for candidate in query_spec.rewrite_candidates
                        if candidate.strip()
                        and candidate.strip().lower() != semantic_query.strip().lower()
                    ),
                    None,
                )
                if alternate:
                    semantic_query = alternate
                    if keyword_query:
                        keyword_query = f"{keyword_query} {alternate}".strip()
                    else:
                        keyword_query = alternate

            if semantic_query:
                # Retry attempt 2: append boost patterns from Retry Planner
                if retry_strategy and retry_strategy.boost_patterns:
                    boost = " ".join(p for p in retry_strategy.boost_patterns if not p.startswith("\\"))
                    if boost:
                        keyword_query = f"{keyword_query or semantic_query} {boost}".strip()
                if not keyword_query:
                    keyword_query = semantic_query
                return QueryRewrite(
                    keyword_query=keyword_query,
                    semantic_query=semantic_query,
                )
        # Fallback: LLM rewriter or rule-based heuristic
        if self._settings.query_rewriter_use_llm:
            from app.services.query_rewriter import rewrite_for_retrieval
            retry_boost = ""
            if retry_strategy and retry_strategy.boost_patterns:
                retry_boost = " ".join(
                    p for p in retry_strategy.boost_patterns if not p.startswith("\\")
                )[:100]
            result = await rewrite_for_retrieval(
                query, conversation_history, retry_boost or None
            )
            return QueryRewrite(
                keyword_query=result.keyword_query,
                semantic_query=result.semantic_query,
                retrieval_profile=result.retrieval_profile,
            )
        # Rule-based heuristic (when query_rewriter_use_llm=False)
        semantic_query = self._rewrite_with_conversation(query, conversation_history)
        q = semantic_query.lower()
        keyword_query = semantic_query
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

    def _build_candidate_pool(
        self,
        merged: list[SearchChunk],
        bm25_ids: set[str],
        vector_ids: set[str],
        extra_ids: set[str],
        stats: dict[str, Any],
        plan: RetrievalPlan | None,
    ) -> CandidatePool:
        """Build CandidatePool from merged chunks (Workstream 3)."""
        items: list[CandidateChunk] = []
        source_counts: dict[str, int] = {"bm25": 0, "vector": 0, "boosted_fetch": 0}
        doc_type_counts: dict[str, int] = {}

        for c in merged:
            if c.chunk_id in extra_ids:
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
        effective_query_spec = None if (retry_strategy and retry_strategy.suggested_query) else query_spec

        # Semantic doc type selection: LLM picks which doc types to search
        if doc_types is None:
            from app.services.archi_config import get_retrieval_doc_type_use_llm
            from app.services.doc_type_router import select_doc_types_for_query
            if get_retrieval_doc_type_use_llm():
                doc_types = await select_doc_types_for_query(effective_query)

        plan = retrieval_plan or build_retrieval_plan(
            effective_query, attempt, effective_query_spec, retry_strategy
        )
        qr = await self._query_rewrite(
            effective_query, conversation_history, retry_strategy, effective_query_spec
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
        # Only override with plan when QuerySpec exists (plan has query_spec values).
        # When no QuerySpec, plan has raw query; keep our LLM/heuristic rewrite.
        if plan.query_keyword and effective_query_spec:
            qr = QueryRewrite(
                keyword_query=plan.query_keyword,
                semantic_query=plan.query_semantic or qr.semantic_query,
                retrieval_profile=qr.retrieval_profile,
            )

        # Use retrieval_profile from LLM rewriter when no QuerySpec (overrides plan heuristic)
        profile = (
            qr.retrieval_profile
            if (qr.retrieval_profile and not effective_query_spec)
            else plan.profile
        )
        effective_doc_types = doc_types or plan.preferred_doc_types
        if retry_strategy and retry_strategy.filter_doc_types:
            effective_doc_types = retry_strategy.filter_doc_types

        fetch_n = plan.fetch_n or top_n or self._settings.retrieval_top_n
        rerank_k = plan.rerank_k or top_k or self._settings.retrieval_top_k
        hard_requirements = self._resolve_hard_requirements(effective_query_spec)
        is_pricing_retrieval = self._is_pricing_retrieval(
            effective_query, profile, effective_query_spec, hard_requirements
        )
        ensure_doc_types = self._derive_ensure_doc_types(
            profile, effective_query_spec, is_pricing_retrieval
        )

        bm25_chunks = await self._opensearch.search(
            query=qr.keyword_query,
            top_n=fetch_n,
            doc_types=effective_doc_types,
            boost_pricing=is_pricing_retrieval or bool(retry_strategy and retry_strategy.boost_patterns),
        )
        bm25_ids = {c.chunk_id for c in bm25_chunks}

        extra_bm25: list[SearchChunk] = []
        if ensure_doc_types:
            extra_bm25 = await self._opensearch.search(
                query=qr.keyword_query,
                top_n=20,
                doc_types=ensure_doc_types,
                boost_pricing=True,
            )
        extra_ids = {c.chunk_id for c in extra_bm25}

        vectors = await self._embedder.embed([qr.semantic_query])
        vector_chunks = await asyncio.to_thread(
            self._qdrant.search,
            vector=vectors[0],
            top_n=fetch_n,
            doc_types=effective_doc_types,
        )
        vector_ids = {c.chunk_id for c in vector_chunks}

        if self._settings.retrieval_fusion == "rrf":
            merged = self._merge_with_rrf(
                bm25_chunks, vector_chunks, k=self._settings.retrieval_rrf_k
            )
        else:
            merged = self._merge_simple(bm25_chunks, vector_chunks)

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

        stats: dict[str, Any] = {
            "bm25_count": len(bm25_chunks),
            "vector_count": len(vector_chunks),
            "merged_count": len(merged),
            "fusion": self._settings.retrieval_fusion,
            "retrieval_profile": profile,
            "query_rewrite": {"keyword_query": qr.keyword_query, "semantic_query": qr.semantic_query},
            "attempt": attempt,
            "plan_reason": plan.reason,
        }
        if hard_requirements:
            stats["hard_requirements"] = sorted(hard_requirements)
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
                    [], set(), set(), set(), stats, plan
                ),
                evidence_set=None,
            )

        extra = 0
        if is_pricing_retrieval:
            extra = max(extra, self._settings.retrieval_plans_extra_chunks)
        if profile == "policy_profile" or "policy_language" in hard_requirements:
            extra = max(extra, 2)
        if profile == "troubleshooting_profile" or "steps_structure" in hard_requirements:
            extra = max(extra, 2)
        rerank_k = min(rerank_k + extra, len(merged))
        reranked = await self._reranker.rerank(effective_query, merged, rerank_k)

        candidate_pool = self._build_candidate_pool(
            merged, bm25_ids, vector_ids, extra_ids, stats, plan
        )

        reranked_search: list[tuple[SearchChunk, float]] = list(reranked)
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
        required_evidence = []
        if effective_query_spec:
            required_evidence = list(
                getattr(effective_query_spec, "hard_requirements", None)
                or effective_query_spec.required_evidence
                or []
            )
        coverage_map: dict[str, str] | None = None
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
