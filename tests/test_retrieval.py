"""Tests for retrieval merge logic."""

import pytest
from unittest.mock import MagicMock, AsyncMock

from app.search.base import SearchChunk
from app.services.retrieval import RetrievalService


def test_merge_simple_by_chunk_id():
    """Simple merge: dedupe by chunk_id, prefer higher score."""
    svc = RetrievalService()
    bm25 = [
        SearchChunk("c1", "d1", "text1", "url1", "policy", 0.8),
        SearchChunk("c2", "d1", "text2", "url1", "policy", 0.6),
    ]
    vector = [
        SearchChunk("c1", "d1", "text1", "url1", "policy", 0.95),  # duplicate, higher score
        SearchChunk("c3", "d2", "text3", "url2", "faq", 0.7),
    ]
    merged = svc._merge_simple(bm25, vector)
    assert len(merged) == 3  # c1, c2, c3
    c1 = next(m for m in merged if m.chunk_id == "c1")
    assert c1.score == 0.95  # prefer higher score


def test_merge_with_rrf():
    """RRF merge: chunks appearing in both lists rank higher."""
    svc = RetrievalService()
    bm25 = [
        SearchChunk("c1", "d1", "text1", "url1", "policy", 0.8),
        SearchChunk("c2", "d1", "text2", "url1", "policy", 0.6),
    ]
    vector = [
        SearchChunk("c1", "d1", "text1", "url1", "policy", 0.95),
        SearchChunk("c3", "d2", "text3", "url2", "faq", 0.7),
    ]
    merged = svc._merge_with_rrf(bm25, vector, k=60)
    assert len(merged) == 3
    # c1 appears in both: RRF = 1/61 + 1/61 > c2 (1/62) or c3 (1/62)
    assert merged[0].chunk_id == "c1"
    assert merged[0].score > merged[1].score


def test_query_rewrite():
    """Query rewrite returns same query for both (simple implementation)."""
    svc = RetrievalService()
    qr = svc._query_rewrite("refund policy")
    assert qr.keyword_query
    assert qr.semantic_query
    assert "refund" in qr.keyword_query
    assert "refund" in qr.semantic_query


def test_query_rewrite_excludes_stopwords_from_context():
    """Context from 'hello what diff' should not add 'hello' to query."""
    svc = RetrievalService()
    qr = svc._query_rewrite(
        "what diff from dedicated and vds?",
        conversation_history=[
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "Hi! How can I help?"},
            {"role": "user", "content": "what diff from dedicated and vds?"},
        ],
    )
    assert "hello" not in qr.keyword_query.lower()
    assert "hello" not in qr.semantic_query.lower()


def test_query_rewrite_with_query_spec():
    """When QuerySpec provided, use its keyword/semantic queries."""
    from app.services.schemas import QuerySpec

    svc = RetrievalService()
    spec = QuerySpec(
        intent="transactional",
        entities=[],
        constraints={},
        required_evidence=[],
        risk_level="low",
        keyword_queries=["custom keyword query"],
        semantic_queries=["custom semantic query"],
        clarifying_questions=[],
        is_ambiguous=False,
    )
    qr = svc._query_rewrite("original query", query_spec=spec)
    assert qr.keyword_query == "custom keyword query"
    assert qr.semantic_query == "custom semantic query"
