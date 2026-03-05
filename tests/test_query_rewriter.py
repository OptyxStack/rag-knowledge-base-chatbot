"""Tests for query rewriter service."""

import pytest
from unittest.mock import AsyncMock, patch

from app.services.query_rewriter import (
    QueryRewriteResult,
    rewrite_for_retrieval,
    _conversation_snippet,
    _cache_key,
    _serialize_result,
    _deserialize_result,
)


def test_conversation_snippet_empty():
    """Empty or short history returns empty snippet."""
    assert _conversation_snippet(None) == ""
    assert _conversation_snippet([]) == ""
    assert _conversation_snippet([{"role": "user", "content": "hi"}]) == ""


def test_conversation_snippet_extracts_last_messages():
    """Extract last 4 messages for cache key."""
    hist = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second question"},
    ]
    snippet = _conversation_snippet(hist)
    assert "first" in snippet
    assert "second" in snippet


def test_cache_key_deterministic():
    """Same inputs produce same cache key."""
    k1 = _cache_key("query", "ctx", "")
    k2 = _cache_key("query", "ctx", "")
    assert k1 == k2


def test_serialize_deserialize_result():
    """JSON serialization round-trip (safe, no pickle)."""
    result = QueryRewriteResult(
        keyword_query="VPS pricing",
        semantic_query="VPS plan pricing",
        retrieval_profile="pricing_profile",
    )
    data = _serialize_result(result)
    assert isinstance(data, str)
    restored = _deserialize_result(data)
    assert restored is not None
    assert restored.keyword_query == result.keyword_query
    assert restored.semantic_query == result.semantic_query
    assert restored.retrieval_profile == result.retrieval_profile


def test_deserialize_invalid_returns_none():
    """Invalid cache data returns None. JSON is safe (no pickle/RCE)."""
    assert _deserialize_result("") is None
    assert _deserialize_result("not json") is None
    # Pickle payload would have been RCE; JSON cannot execute code
    assert _deserialize_result("c__builtin__\neval\n(S'1+1'\ntR.") is None


def test_cache_key_different_for_different_inputs():
    """Different inputs produce different cache keys."""
    k1 = _cache_key("query1", "", "")
    k2 = _cache_key("query2", "", "")
    assert k1 != k2


@pytest.mark.asyncio
async def test_rewrite_for_retrieval_disabled_returns_query_as_is(monkeypatch):
    """When query_rewriter_use_llm=False, return query as-is."""
    monkeypatch.setattr(
        "app.services.query_rewriter.get_settings",
        lambda: type("S", (), {"query_rewriter_use_llm": False})(),
    )
    result = await rewrite_for_retrieval("vps plans price")
    assert result.keyword_query == "vps plans price"
    assert result.semantic_query == "vps plans price"
    assert result.retrieval_profile == "generic_profile"


@pytest.mark.asyncio
async def test_rewrite_for_retrieval_llm_success(monkeypatch):
    """When LLM succeeds, return parsed result."""
    mock_response = type("R", (), {
        "content": '{"keyword_query": "VPS pricing plans", "semantic_query": "VPS plan pricing", "retrieval_profile": "pricing_profile"}',
    })()
    monkeypatch.setattr(
        "app.services.query_rewriter.get_settings",
        lambda: type("S", (), {
            "query_rewriter_use_llm": True,
            "query_rewriter_cache_enabled": False,
        })(),
    )
    with patch("app.services.query_rewriter.get_llm_gateway") as mock_gw:
        mock_chat = AsyncMock(return_value=mock_response)
        mock_gw.return_value.chat = mock_chat
        with patch("app.services.query_rewriter.get_model_for_task", return_value="gpt-4o-mini"):
            result = await rewrite_for_retrieval("vps plans")
    assert result.keyword_query == "VPS pricing plans"
    assert result.semantic_query == "VPS plan pricing"
    assert result.retrieval_profile == "pricing_profile"


@pytest.mark.asyncio
async def test_rewrite_for_retrieval_llm_fallback_on_error(monkeypatch):
    """When LLM fails, return query as-is."""
    monkeypatch.setattr(
        "app.services.query_rewriter.get_settings",
        lambda: type("S", (), {
            "query_rewriter_use_llm": True,
            "query_rewriter_cache_enabled": False,
        })(),
    )
    with patch("app.services.query_rewriter.get_llm_gateway") as mock_gw:
        mock_gw.return_value.chat = AsyncMock(side_effect=Exception("LLM error"))
        result = await rewrite_for_retrieval("refund policy")
    assert result.keyword_query == "refund policy"
    assert result.semantic_query == "refund policy"
    assert result.retrieval_profile == "generic_profile"
