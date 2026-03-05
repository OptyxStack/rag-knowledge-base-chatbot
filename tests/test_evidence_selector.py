"""Tests for evidence selector service."""

import pytest
from unittest.mock import AsyncMock, patch

from app.search.base import SearchChunk
from app.services.evidence_selector import (
    EvidenceSelectionResult,
    select_evidence_for_query,
)


def _make_chunk(cid: str, text: str = "content", doc_type: str = "faq", url: str = "") -> SearchChunk:
    return SearchChunk(
        chunk_id=cid,
        document_id="d1",
        chunk_text=text,
        source_url=url or f"https://example.com/{cid}",
        doc_type=doc_type,
        score=0.8,
    )


@pytest.mark.asyncio
async def test_select_evidence_empty_reranked():
    """Empty reranked returns empty selection."""
    result = await select_evidence_for_query("query", [], required_evidence=["numbers_units"])
    assert result.selected == []
    assert result.uncovered_requirements == ["numbers_units"]


@pytest.mark.asyncio
async def test_select_evidence_disabled_uses_top_k(monkeypatch):
    """When evidence_selector_use_llm=False, return top-k."""
    monkeypatch.setattr(
        "app.services.evidence_selector.get_settings",
        lambda: type("S", (), {"evidence_selector_use_llm": False})(),
    )
    chunks = [
        (_make_chunk("c1"), 0.9),
        (_make_chunk("c2"), 0.8),
        (_make_chunk("c3"), 0.7),
        (_make_chunk("c4"), 0.6),
        (_make_chunk("c5"), 0.5),
    ]
    result = await select_evidence_for_query("query", chunks, top_k_fallback=3)
    assert len(result.selected) == 3
    assert result.selected[0][0].chunk_id == "c1"
    assert result.used_llm is False


@pytest.mark.asyncio
async def test_select_evidence_llm_success(monkeypatch):
    """When LLM succeeds, return selected chunks."""
    monkeypatch.setattr(
        "app.services.evidence_selector.get_settings",
        lambda: type("S", (), {"evidence_selector_use_llm": True})(),
    )
    chunks = [
        (_make_chunk("c1", doc_type="pricing"), 0.9),
        (_make_chunk("c2", doc_type="faq"), 0.8),
        (_make_chunk("c3", doc_type="policy"), 0.7),
    ]
    mock_resp = type("R", (), {
        "content": '{"selected_chunk_ids": ["c1", "c3"], "coverage_map": {"numbers_units": "c1"}, "uncovered_requirements": [], "reasoning": "selected pricing and policy"}',
    })()
    with patch("app.services.evidence_selector.get_llm_gateway") as mock_gw:
        mock_gw.return_value.chat = AsyncMock(return_value=mock_resp)
        with patch("app.services.evidence_selector.get_model_for_task", return_value="gpt-4o-mini"):
            result = await select_evidence_for_query("query", chunks, required_evidence=["numbers_units"])
    assert len(result.selected) == 2
    assert result.selected[0][0].chunk_id == "c1"
    assert result.selected[1][0].chunk_id == "c3"
    assert result.coverage_map == {"numbers_units": "c1"}
    assert result.used_llm is True


@pytest.mark.asyncio
async def test_select_evidence_llm_fallback_on_error(monkeypatch):
    """When LLM fails, fallback to top-k."""
    monkeypatch.setattr(
        "app.services.evidence_selector.get_settings",
        lambda: type("S", (), {"evidence_selector_use_llm": True})(),
    )
    chunks = [
        (_make_chunk("c1"), 0.9),
        (_make_chunk("c2"), 0.8),
        (_make_chunk("c3"), 0.7),
    ]
    with patch("app.services.evidence_selector.get_llm_gateway") as mock_gw:
        mock_gw.return_value.chat = AsyncMock(side_effect=Exception("LLM error"))
        result = await select_evidence_for_query("query", chunks, top_k_fallback=2)
    assert len(result.selected) == 2
    assert result.selected[0][0].chunk_id == "c1"
    assert result.used_llm is False


@pytest.mark.asyncio
async def test_select_evidence_llm_invalid_ids_fallback(monkeypatch):
    """When LLM returns invalid chunk IDs, fallback to top-k."""
    monkeypatch.setattr(
        "app.services.evidence_selector.get_settings",
        lambda: type("S", (), {"evidence_selector_use_llm": True})(),
    )
    chunks = [
        (_make_chunk("c1"), 0.9),
        (_make_chunk("c2"), 0.8),
    ]
    mock_resp = type("R", (), {
        "content": '{"selected_chunk_ids": ["invalid_id"], "coverage_map": {}, "uncovered_requirements": [], "reasoning": "bad"}',
    })()
    with patch("app.services.evidence_selector.get_llm_gateway") as mock_gw:
        mock_gw.return_value.chat = AsyncMock(return_value=mock_resp)
        with patch("app.services.evidence_selector.get_model_for_task", return_value="gpt-4o-mini"):
            result = await select_evidence_for_query("query", chunks, top_k_fallback=2)
    assert len(result.selected) == 2  # fallback
    assert result.used_llm is True  # LLM was called but returned invalid
