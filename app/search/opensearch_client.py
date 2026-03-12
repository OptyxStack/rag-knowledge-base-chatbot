"""
OpenSearch retrieval client (production-grade-ish).

Features:
- AsyncOpenSearch if available; sync fallback via asyncio.to_thread
- Index bootstrap with safer analysis + field mapping + optional env overrides
- Bulk indexing (fast) + sane refresh policy (no refresh=True per doc)
- BM25 query that is more robust: best_fields + phrase boost + mild fuzziness + MSM
- Optional boosts: doc_type, recency (effective_date), and business flags (boost_pricing)
- Optional highlight/snippet extraction to reduce prompt bloat
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Iterable

from opensearchpy import OpenSearch
from opensearchpy.helpers import bulk

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import SearchChunk

try:
    from opensearchpy import AsyncOpenSearch
except ImportError:
    AsyncOpenSearch = None  # Fallback to sync client with asyncio.to_thread

logger = get_logger(__name__)


# -----------------------------
# Index config
# -----------------------------

DEFAULT_INDEX_SETTINGS: dict[str, Any] = {
    "settings": {
        "index": {
            # NOTE: shards/replicas should be env-configurable in real prod.
            "number_of_shards": 1,
            "number_of_replicas": 0,
            # Keep defaults sane; you can tune after measuring.
            "refresh_interval": "1s",
        },
        "analysis": {
            "filter": {
                # If you need VN support, consider a dedicated analyzer/tokenizer.
                "en_stop": {"type": "stop", "stopwords": "_english_"},
                "en_stemmer": {"type": "stemmer", "language": "english"},
                "synonym_filter": {
                    "type": "synonym",
                    # NOTE: editing synonyms requires reindex unless you use synonym sets / reloadable synonyms.
                    "synonyms": [
                        "refund, return, money back",
                        "billing, invoice, payment",
                        "cancel, cancellation, terminate",
                        "upgrade, upsize, scale up",
                        "downgrade, scale down",
                    ],
                },
            },
            "analyzer": {
                "index_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "en_stop", "en_stemmer", "synonym_filter"],
                },
                "search_analyzer": {
                    "type": "custom",
                    "tokenizer": "standard",
                    "filter": ["lowercase", "en_stop", "en_stemmer", "synonym_filter"],
                },
            },
        },
    },
    "mappings": {
        "dynamic": "false",
        "properties": {
            "chunk_id": {"type": "keyword"},
            "document_id": {"type": "keyword"},
            "doc_type": {"type": "keyword"},
            "page_kind": {"type": "keyword"},
            "product_family": {"type": "keyword"},
            "source_url": {"type": "keyword"},
            "effective_date": {"type": "date"},
            # Text fields for BM25.
            "title": {
                "type": "text",
                "analyzer": "index_analyzer",
                "search_analyzer": "search_analyzer",
            },
            "headings": {
                "type": "text",
                "analyzer": "index_analyzer",
                "search_analyzer": "search_analyzer",
            },
            # Keep body searchable, but consider storing smaller chunk_text for prompt usage.
            "body": {
                "type": "text",
                "analyzer": "index_analyzer",
                "search_analyzer": "search_analyzer",
            },
            "chunk_text": {
                "type": "text",
                "analyzer": "index_analyzer",
                "search_analyzer": "search_analyzer",
            },
        },
    },
}


# -----------------------------
# Helpers
# -----------------------------


def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Shallow-ish merge for nested dicts (good enough for settings overrides)."""
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge_dict(out[k], v)  # type: ignore[arg-type]
        else:
            out[k] = v
    return out


def _now_ms() -> int:
    return int(time.time() * 1000)


# -----------------------------
# Client
# -----------------------------


class OpenSearchClient:
    """OpenSearch client for BM25 retrieval with production-friendly defaults."""

    def __init__(self) -> None:
        self._client: Any = None
        self._sync_client: OpenSearch | None = None
        self._settings = get_settings()

    async def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        host = self._settings.opensearch_host
        use_ssl = host.startswith("https")

        http_auth = (
            (self._settings.opensearch_user, self._settings.opensearch_password)
            if getattr(self._settings, "opensearch_user", None)
            else None
        )

        if AsyncOpenSearch is not None:
            self._client = AsyncOpenSearch(
                hosts=[host],
                http_auth=http_auth,
                use_ssl=use_ssl,
                verify_certs=True,
                # You can set timeout/retries if your env needs it.
                timeout=30,
                max_retries=3,
                retry_on_timeout=True,
            )
            return self._client

        if self._sync_client is None:
            self._sync_client = OpenSearch(
                hosts=[host],
                http_auth=http_auth,
                use_ssl=use_ssl,
                verify_certs=True,
                timeout=30,
                max_retries=3,
                retry_on_timeout=True,
            )
        return self._sync_client

    def _is_async(self) -> bool:
        return self._client is not None

    async def ensure_index(self, *, settings_override: dict[str, Any] | None = None) -> None:
        """Create index if it does not exist."""
        client = await self._get_client()
        index_name = self._settings.opensearch_index

        body = DEFAULT_INDEX_SETTINGS
        if settings_override:
            body = _merge_dict(body, settings_override)

        if self._is_async():
            exists = await client.indices.exists(index=index_name)
            if not exists:
                await client.indices.create(index=index_name, body=body)
                logger.info("opensearch_index_created", index=index_name)
        else:
            exists = await asyncio.to_thread(client.indices.exists, index=index_name)
            if not exists:
                await asyncio.to_thread(client.indices.create, index=index_name, body=body)
                logger.info("opensearch_index_created", index=index_name)

    # -----------------------------
    # Indexing
    # -----------------------------

    async def index_chunk(
        self,
        chunk_id: str,
        document_id: str,
        title: str,
        headings: str,
        body: str,
        doc_type: str,
        source_url: str,
        effective_date: str | None,
        chunk_text: str,
        metadata: dict[str, Any] | None = None,
        *,
        refresh: str | bool | None = None,
    ) -> None:
        """
        Index a single chunk.

        IMPORTANT:
        - Avoid refresh=True on every write in production.
        - Prefer bulk_index_chunks().
        """
        client = await self._get_client()
        index_name = self._settings.opensearch_index

        doc = {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "title": title,
            "headings": headings,
            "body": body,
            "doc_type": doc_type,
            "source_url": source_url,
            "effective_date": effective_date,
            "chunk_text": chunk_text,
        }
        if isinstance(metadata, dict):
            if metadata.get("page_kind"):
                doc["page_kind"] = str(metadata.get("page_kind")).strip().lower()
            if metadata.get("product_family"):
                doc["product_family"] = str(metadata.get("product_family")).strip().lower()

        kwargs: dict[str, Any] = {}
        if refresh is not None:
            kwargs["refresh"] = refresh

        if self._is_async():
            await client.index(index=index_name, id=chunk_id, body=doc, **kwargs)
        else:
            await asyncio.to_thread(client.index, index=index_name, id=chunk_id, body=doc, **kwargs)

    async def bulk_index_chunks(
        self,
        docs: Iterable[dict[str, Any]],
        *,
        refresh: str | bool | None = "wait_for",
        chunk_size: int = 500,
        request_timeout: int = 60,
    ) -> tuple[int, int]:
        """
        Bulk index many chunks quickly.

        docs item expected keys:
          - chunk_id (used as _id)
          - other mapped fields

        Returns (success_count, fail_count)
        """
        client = await self._get_client()
        index_name = self._settings.opensearch_index

        actions = []
        for d in docs:
            cid = d.get("chunk_id") or d.get("_id")
            if not cid:
                continue
            actions.append(
                {
                    "_op_type": "index",
                    "_index": index_name,
                    "_id": cid,
                    "_source": d,
                }
            )

        if not actions:
            return (0, 0)

        def _bulk_sync() -> tuple[int, int]:
            ok, errors = bulk(
                client,  # type: ignore[arg-type]
                actions,
                chunk_size=chunk_size,
                request_timeout=request_timeout,
                refresh=refresh,
                raise_on_error=False,
            )
            fail = len(errors or [])
            return (int(ok), int(fail))

        try:
            if self._is_async():
                # opensearch-py async client doesn't ship an async bulk helper consistently.
                # Run bulk in a thread even if we have AsyncOpenSearch.
                ok, fail = await asyncio.to_thread(_bulk_sync)
            else:
                ok, fail = await asyncio.to_thread(_bulk_sync)

            logger.info("opensearch_bulk_index_done", ok=ok, fail=fail, index=index_name)
            return (ok, fail)
        except Exception as e:
            logger.error("opensearch_bulk_index_failed", error=str(e), index=index_name)
            return (0, len(actions))

    async def delete_chunk(self, chunk_id: str, *, refresh: str | bool | None = None) -> None:
        """Delete a chunk by ID (no-op if missing)."""
        client = await self._get_client()
        index_name = self._settings.opensearch_index

        kwargs: dict[str, Any] = {}
        if refresh is not None:
            kwargs["refresh"] = refresh

        try:
            if self._is_async():
                await client.delete(index=index_name, id=chunk_id, ignore=[404], **kwargs)
            else:
                await asyncio.to_thread(client.delete, index=index_name, id=chunk_id, ignore=[404], **kwargs)
        except Exception as e:
            logger.warning("opensearch_delete_failed", chunk_id=chunk_id, error=str(e))

    # -----------------------------
    # Search
    # -----------------------------

    async def search(
        self,
        query: str,
        *,
        top_n: int = 50,
        doc_types: list[str] | None = None,
        boost_pricing: bool = False,
        use_highlight: bool = True,
        prefer_snippet: bool = True,
        snippet_max_chars: int = 900,
        recency_scale: str = "45d",
        min_should_match: str = "70%",
        fuzziness: str | None = "AUTO",
        page_kinds: list[str] | None = None,
        product_families: list[str] | None = None,
        page_kind_weights: dict[str, float] | None = None,
        product_family_weights: dict[str, float] | None = None,
    ) -> list[SearchChunk]:
        """
        BM25 search with a more robust query structure.

        Returns SearchChunk list with:
          - chunk_text: full chunk text by default when prefer_snippet=False,
            otherwise snippet if highlight available
          - score: OpenSearch _score
        """
        client = await self._get_client()
        index_name = self._settings.opensearch_index

        filters: list[dict[str, Any]] = []
        if doc_types:
            filters.append({"terms": {"doc_type": doc_types}})
        if page_kinds:
            normalized_page_kinds = [str(k).strip().lower() for k in page_kinds if str(k).strip()]
            if normalized_page_kinds:
                filters.append({"terms": {"page_kind": normalized_page_kinds}})
        if product_families:
            normalized_families = [str(k).strip().lower() for k in product_families if str(k).strip()]
            if normalized_families:
                filters.append({"terms": {"product_family": normalized_families}})

        # Stronger-than-MVP query:
        # - best_fields for general matching (MSM helps long queries)
        # - phrase boost for exact-ish hits
        # - mild fuzziness on title/headings for typos (optional)
        dis_max_queries: list[dict[str, Any]] = [
            {
                "multi_match": {
                    "query": query,
                    "fields": [
                        "title^3.0",
                        "headings^2.0",
                        "chunk_text^1.6",
                        "body^1.0",
                    ],
                    "type": "best_fields",
                    "operator": "or",
                    "minimum_should_match": min_should_match,
                }
            },
            {
                "multi_match": {
                    "query": query,
                    "fields": ["title^5.0", "headings^3.5", "chunk_text^2.0"],
                    "type": "phrase",
                    "slop": 2,
                    "boost": 2.0,
                }
            },
        ]

        if fuzziness:
            dis_max_queries.append(
                {
                    "multi_match": {
                        "query": query,
                        "fields": ["title^2.0", "headings^1.5"],
                        "fuzziness": fuzziness,
                        "prefix_length": 1,
                        "max_expansions": 50,
                        "boost": 0.35,
                    }
                }
            )

        base_bool = {
            "must": [{"dis_max": {"queries": dis_max_queries}}],
            "filter": filters,
            "should": [],
        }

        # Business-aware boosts
        if boost_pricing:
            base_bool["should"].append({"term": {"doc_type": {"value": "pricing", "boost": 2.0}}})
        if page_kind_weights:
            for page_kind, weight in page_kind_weights.items():
                kind = str(page_kind).strip().lower()
                if not kind:
                    continue
                try:
                    boost = float(weight)
                except (TypeError, ValueError):
                    continue
                if boost <= 0:
                    continue
                base_bool["should"].append({"term": {"page_kind": {"value": kind, "boost": boost}}})
        if product_family_weights:
            for family, weight in product_family_weights.items():
                normalized = str(family).strip().lower()
                if not normalized:
                    continue
                try:
                    boost = float(weight)
                except (TypeError, ValueError):
                    continue
                if boost <= 0:
                    continue
                base_bool["should"].append({"term": {"product_family": {"value": normalized, "boost": boost}}})

        # Function score: recency boost using effective_date (if present).
        # This helps prevent outdated policy/pricing chunks outranking newer ones.
        query_body: dict[str, Any] = {
            "function_score": {
                "query": {"bool": base_bool},
                "functions": [
                    {
                        "gauss": {
                            "effective_date": {
                                "origin": "now",
                                "scale": recency_scale,
                                "decay": 0.5,
                            }
                        }
                    }
                ],
                "score_mode": "sum",
                "boost_mode": "sum",
            }
        }

        body: dict[str, Any] = {
            "size": top_n,
            "query": query_body,
            "_source": [
                "chunk_id",
                "document_id",
                "chunk_text",
                "source_url",
                "doc_type",
                "page_kind",
                "product_family",
                "effective_date",
                "title",
            ],
        }

        if use_highlight:
            body["highlight"] = {
                "pre_tags": ["<em>"],
                "post_tags": ["</em>"],
                "fields": {
                    "chunk_text": {"fragment_size": 180, "number_of_fragments": 3},
                    "title": {"fragment_size": 120, "number_of_fragments": 1},
                    "headings": {"fragment_size": 120, "number_of_fragments": 1},
                    "body": {"fragment_size": 180, "number_of_fragments": 2},
                },
            }

        start = _now_ms()
        try:
            if self._is_async():
                response = await client.search(index=index_name, body=body)
            else:
                response = await asyncio.to_thread(client.search, index=index_name, body=body)
        except Exception as e:
            logger.error("opensearch_search_failed", query=query, error=str(e))
            return []
        finally:
            logger.info("opensearch_search_latency_ms", ms=_now_ms() - start, top_n=top_n)

        hits = response.get("hits", {}).get("hits", [])
        out: list[SearchChunk] = []

        for hit in hits:
            src = hit.get("_source", {}) or {}
            score = float(hit.get("_score") or 0.0)

            # Prefer highlight snippets to reduce prompt bloat.
            snippet = ""
            if use_highlight:
                hl = hit.get("highlight", {}) or {}
                # Try chunk_text first, then body/title/headings as fallback
                for field in ("chunk_text", "body", "title", "headings"):
                    frags = hl.get(field)
                    if frags:
                        snippet = " … ".join(frags)
                        break

            snippet_text = snippet.strip() if snippet else ""
            if snippet_text and snippet_max_chars and len(snippet_text) > snippet_max_chars:
                snippet_text = snippet_text[: snippet_max_chars - 1] + "…"
            full_text = (src.get("chunk_text") or "")
            chunk_text = (
                snippet_text
                if (prefer_snippet and snippet_text)
                else (full_text or snippet_text)
            )
            # Keep snippets for debug/UI while preserving full chunk_text for rerank/evidence when requested.
            metadata = dict(src)
            if snippet_text:
                metadata["highlight_snippet"] = snippet_text

            out.append(
                SearchChunk(
                    chunk_id=src.get("chunk_id") or hit.get("_id") or "",
                    document_id=src.get("document_id") or "",
                    chunk_text=chunk_text,
                    source_url=src.get("source_url") or "",
                    doc_type=src.get("doc_type") or "other",
                    score=score,
                    metadata=metadata,
                )
            )

        return out

    async def close(self) -> None:
        """Close clients."""
        if self._client and self._is_async():
            await self._client.close()
        self._client = None
        self._sync_client = None


def get_opensearch_client() -> OpenSearchClient:
    """Factory for dependency injection."""
    return OpenSearchClient()
