"""Qdrant client for vector similarity search."""

from qdrant_client import QdrantClient
from qdrant_client.http import models as qdrant_models

from app.core.config import get_settings
from app.core.logging import get_logger
from app.search.base import SearchChunk

logger = get_logger(__name__)


class QdrantSearchClient:
    """Qdrant client for semantic search."""

    def __init__(self) -> None:
        self._client: QdrantClient | None = None
        self._settings = get_settings()

    def _get_client(self) -> QdrantClient:
        if self._client is None:
            self._client = QdrantClient(
                host=self._settings.qdrant_host,
                port=self._settings.qdrant_port,
                api_key=self._settings.qdrant_api_key or None,
                check_compatibility=False,
            )
        return self._client

    def ensure_collection(self, dimensions: int) -> None:
        """Create collection if not exists."""
        client = self._get_client()
        collections = client.get_collections().collections
        if not any(c.name == self._settings.qdrant_collection for c in collections):
            client.create_collection(
                collection_name=self._settings.qdrant_collection,
                vectors_config=qdrant_models.VectorParams(
                    size=dimensions,
                    distance=qdrant_models.Distance.COSINE,
                ),
                optimizers_config=qdrant_models.OptimizersConfigDiff(
                    indexing_threshold=10000,
                ),
            )
            logger.info(
                "qdrant_collection_created",
                collection=self._settings.qdrant_collection,
                dimensions=dimensions,
            )

    def upsert_chunk(
        self,
        chunk_id: str,
        vector: list[float],
        chunk_text: str,
        document_id: str,
        source_url: str,
        doc_type: str,
        metadata: dict | None = None,
    ) -> None:
        """Upsert a chunk with vector and metadata."""
        client = self._get_client()
        payload = {
            "chunk_id": chunk_id,
            "document_id": document_id,
            "chunk_text": chunk_text,
            "source_url": source_url,
            "doc_type": doc_type,
            **(metadata or {}),
        }
        payload = {k: v for k, v in payload.items() if v is not None}

        client.upsert(
            collection_name=self._settings.qdrant_collection,
            points=[
                qdrant_models.PointStruct(
                    id=chunk_id,
                    vector=vector,
                    payload=payload,
                )
            ],
        )

    def delete_chunk(self, chunk_id: str) -> None:
        """Delete a chunk by ID."""
        client = self._get_client()
        try:
            client.delete(
                collection_name=self._settings.qdrant_collection,
                points_selector=qdrant_models.PointIdsList(points=[chunk_id]),
            )
        except Exception as e:
            logger.warning("qdrant_delete_failed", chunk_id=chunk_id, error=str(e))

    def search(
        self,
        vector: list[float],
        top_n: int = 50,
        doc_types: list[str] | None = None,
        page_kinds: list[str] | None = None,
        product_families: list[str] | None = None,
    ) -> list[SearchChunk]:
        """Vector similarity search."""
        client = self._get_client()

        must_filters: list[qdrant_models.FieldCondition] = []
        if doc_types:
            must_filters.append(
                qdrant_models.FieldCondition(
                    key="doc_type",
                    match=qdrant_models.MatchAny(any=doc_types),
                )
            )
        if page_kinds:
            normalized = [str(v).strip().lower() for v in page_kinds if str(v).strip()]
            if normalized:
                must_filters.append(
                    qdrant_models.FieldCondition(
                        key="page_kind",
                        match=qdrant_models.MatchAny(any=normalized),
                    )
                )
        if product_families:
            normalized = [str(v).strip().lower() for v in product_families if str(v).strip()]
            if normalized:
                must_filters.append(
                    qdrant_models.FieldCondition(
                        key="product_family",
                        match=qdrant_models.MatchAny(any=normalized),
                    )
                )
        filter_condition = qdrant_models.Filter(must=must_filters) if must_filters else None

        try:
            # Use query_points (new API); search() was removed in qdrant-client 1.7+
            resp = client.query_points(
                collection_name=self._settings.qdrant_collection,
                query=vector,
                limit=top_n,
                query_filter=filter_condition,
                with_payload=True,
            )
            results = resp.points
        except Exception as e:
            logger.error("qdrant_search_failed", error=str(e))
            return []

        chunks: list[SearchChunk] = []
        for hit in results:
            payload = hit.payload or {}
            chunks.append(
                SearchChunk(
                    chunk_id=payload.get("chunk_id", str(hit.id)),
                    document_id=payload.get("document_id", ""),
                    chunk_text=payload.get("chunk_text", ""),
                    source_url=payload.get("source_url", ""),
                    doc_type=payload.get("doc_type", "other"),
                    score=1.0 - hit.score if hit.score else 0.0,  # Cosine distance -> similarity
                    metadata=payload,
                )
            )
        return chunks

    def close(self) -> None:
        """Close client."""
        self._client = None


def get_qdrant_client() -> QdrantSearchClient:
    """Factory for dependency injection."""
    return QdrantSearchClient()
