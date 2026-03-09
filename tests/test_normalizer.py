"""Tests for Phase 2 Normalizer (LLM-only)."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.config import get_settings
from app.services.normalizer import normalize
from app.services.schemas import QuerySpec


def _mock_llm_response(data: dict) -> MagicMock:
    """Build mock LLM response with JSON content."""
    resp = MagicMock()
    resp.content = json.dumps(data)
    return resp


@pytest.fixture(autouse=True)
def _reset_normalizer_settings(monkeypatch):
    monkeypatch.setenv("NORMALIZER_USE_LLM", "true")
    monkeypatch.delenv("NORMALIZER_DOMAIN_TERMS", raising=False)
    monkeypatch.setenv("NORMALIZER_QUERY_EXPANSION", "false")
    monkeypatch.setenv("NORMALIZER_SLOTS_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_uses_authoritative_retrieval_fields(mock_get_gateway):
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "refund policy for VPS",
            "intent": "policy",
            "entities": ["vps"],
            "required_evidence": ["policy_language", "has_any_url"],
            "hard_requirements": ["policy_language"],
            "soft_requirements": ["has_any_url"],
            "retrieval_profile": "policy_profile",
            "doc_type_prior": ["policy", "tos"],
            "risk_level": "high",
            "is_ambiguous": False,
            "clarifying_questions": [],
            "retrieval_rewrites": ["vps refund policy"],
            "skip_retrieval": False,
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize("refund policy for VPS")
    assert spec.retrieval_profile == "policy_profile"
    assert spec.hard_requirements == ["policy_language"]
    assert spec.soft_requirements == ["has_any_url"]
    assert spec.doc_type_prior == ["policy", "tos"]


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_infers_retrieval_profile_and_hard_requirements(mock_get_gateway):
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "vps pricing and order link",
            "intent": "transactional",
            "entities": ["vps"],
            "required_evidence": ["numbers_units", "transaction_link"],
            "risk_level": "medium",
            "is_ambiguous": False,
            "clarifying_questions": [],
            "retrieval_rewrites": ["vps pricing order"],
            "skip_retrieval": False,
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize("vps pricing and order link")
    assert spec.retrieval_profile == "pricing_profile"
    assert set(spec.hard_requirements or []) == {"numbers_units", "transaction_link"}


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_transactional(mock_get_gateway):
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "what is the price of VPS?",
            "intent": "transactional",
            "entities": ["vps"],
            "required_evidence": ["numbers_units", "transaction_link"],
            "risk_level": "low",
            "is_ambiguous": False,
            "clarifying_questions": [],
            "retrieval_rewrites": ["VPS pricing", "VPS price"],
            "skip_retrieval": False,
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize("what is the price of VPS?")
    assert spec.intent == "transactional"
    assert "numbers_units" in (spec.required_evidence or [])
    assert not spec.is_ambiguous
    assert spec.keyword_queries
    assert spec.semantic_queries
    assert spec.extraction_mode == "llm_primary"


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_policy(mock_get_gateway):
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "refund policy",
            "intent": "policy",
            "entities": [],
            "required_evidence": ["policy_language"],
            "risk_level": "medium",
            "is_ambiguous": False,
            "clarifying_questions": [],
            "retrieval_rewrites": ["refund policy", "refund terms"],
            "skip_retrieval": False,
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize("refund policy")
    assert spec.intent == "policy"
    assert "policy_language" in (spec.required_evidence or [])
    assert "policy_language" in (spec.soft_requirements or [])
    assert spec.risk_level in ("low", "medium", "high")


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_ambiguous_with_pasted_content(mock_get_gateway):
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "what diff from this?",
            "intent": "ambiguous",
            "entities": [],
            "required_evidence": [],
            "risk_level": "low",
            "is_ambiguous": True,
            "clarifying_questions": [
                "What would you like to compare this with?",
                "Could you specify what 'this' refers to?",
            ],
            "retrieval_rewrites": [],
            "skip_retrieval": False,
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize(
        "what diff from this? 1) Dedicated Server... [bc387f35 | https://...]",
        conversation_history=[
            {"role": "user", "content": "what diff from ur dedicated server and vds?"},
            {"role": "assistant", "content": "Dedicated Server (bare-metal)... VDS (Virtual)..."},
        ],
    )
    assert spec.is_ambiguous
    assert spec.intent == "ambiguous"
    assert spec.clarifying_questions


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_ambiguous_short_query_with_long_assistant(mock_get_gateway):
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "what diff from this?",
            "intent": "ambiguous",
            "entities": [],
            "required_evidence": [],
            "risk_level": "low",
            "is_ambiguous": True,
            "clarifying_questions": ["What would you like to compare?"],
            "retrieval_rewrites": [],
            "skip_retrieval": False,
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize(
        "what diff from this?",
        conversation_history=[
            {"role": "user", "content": "dedicated vs vds?"},
            {"role": "assistant", "content": "A" * 200},
        ],
    )
    assert spec.is_ambiguous


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_not_ambiguous_without_context(mock_get_gateway):
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "what diff from this?",
            "intent": "informational",
            "entities": [],
            "required_evidence": [],
            "risk_level": "low",
            "is_ambiguous": False,
            "clarifying_questions": [],
            "retrieval_rewrites": ["difference", "compare"],
            "skip_retrieval": False,
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize("what diff from this?")
    assert not spec.is_ambiguous


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_entities_default_generic_mode(mock_get_gateway):
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "VPS pricing for Windows and Linux",
            "intent": "transactional",
            "entities": [],
            "required_evidence": ["numbers_units"],
            "risk_level": "low",
            "is_ambiguous": False,
            "clarifying_questions": [],
            "retrieval_rewrites": ["VPS pricing", "Windows Linux"],
            "skip_retrieval": False,
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize("VPS pricing for Windows and Linux")
    assert spec.entities == [] or "vps" in [e.lower() for e in spec.entities]
    assert spec.resolved_slots == {} or spec.resolved_slots is not None


@pytest.mark.asyncio
async def test_normalize_compatibility_mode_uses_domain_overrides(monkeypatch):
    monkeypatch.setenv("NORMALIZER_DOMAIN_TERMS", "vps,windows,linux")
    monkeypatch.setenv("NORMALIZER_SLOTS_ENABLED", "true")
    monkeypatch.setenv("NORMALIZER_SLOT_PRODUCT_TYPES", "vps,dedicated,vds")
    monkeypatch.setenv("NORMALIZER_SLOT_OS_TYPES", "windows,linux,macos")
    monkeypatch.setenv("NORMALIZER_QUERY_EXPANSION", "true")
    get_settings.cache_clear()

    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "VPS pricing for Windows and Linux",
            "intent": "transactional",
            "entities": ["vps", "windows", "linux"],
            "required_evidence": ["numbers_units"],
            "risk_level": "low",
            "is_ambiguous": False,
            "clarifying_questions": [],
            "retrieval_rewrites": ["VPS pricing", "Windows Linux"],
            "skip_retrieval": False,
        })
    )

    with patch("app.services.normalizer.get_llm_gateway", return_value=mock_gateway):
        spec = await normalize("VPS pricing for Windows and Linux")

    assert "vps" in [e.lower() for e in spec.entities]
    assert spec.resolved_slots is not None
    assert spec.resolved_slots.get("product_type") == "vps"
    assert spec.resolved_slots.get("os") == "windows"
    assert "normalizer_domain_terms" in (spec.config_overrides_applied or [])
    assert "normalizer_query_expansion" in (spec.config_overrides_applied or [])
    assert "normalizer_slots_enabled" in (spec.config_overrides_applied or [])
    assert "normalizer_slot_product_types" in (spec.config_overrides_applied or [])
    assert "normalizer_slot_os_types" in (spec.config_overrides_applied or [])


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_llm_infers_slots(mock_get_gateway):
    """LLM can infer product_type, os, etc. from query without config."""
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "VPS pricing for Windows",
            "intent": "transactional",
            "entities": ["vps", "windows"],
            "required_evidence": ["numbers_units"],
            "risk_level": "low",
            "is_ambiguous": False,
            "clarifying_questions": [],
            "retrieval_rewrites": ["VPS pricing Windows"],
            "skip_retrieval": False,
            "product_type": "vps",
            "os": "windows",
            "billing_cycle": "monthly",
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize("VPS pricing for Windows")

    assert spec.resolved_slots.get("product_type") == "vps"
    assert spec.resolved_slots.get("os") == "windows"
    assert spec.resolved_slots.get("billing_cycle") == "monthly"


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_slots_disabled_when_config_empty(mock_get_gateway, monkeypatch):
    """When slots enabled but slot_product_types/slot_os_types empty, no product/os slots extracted."""
    monkeypatch.setenv("NORMALIZER_DOMAIN_TERMS", "vps,windows,linux")
    monkeypatch.setenv("NORMALIZER_SLOTS_ENABLED", "true")
    monkeypatch.delenv("NORMALIZER_SLOT_PRODUCT_TYPES", raising=False)
    monkeypatch.delenv("NORMALIZER_SLOT_OS_TYPES", raising=False)
    get_settings.cache_clear()

    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "VPS pricing for Windows and Linux",
            "intent": "transactional",
            "entities": ["vps", "windows", "linux"],
            "required_evidence": ["numbers_units"],
            "risk_level": "low",
            "is_ambiguous": False,
            "clarifying_questions": [],
            "retrieval_rewrites": ["VPS pricing"],
            "skip_retrieval": False,
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize("VPS pricing for Windows and Linux")

    assert spec.resolved_slots is not None
    assert "product_type" not in spec.resolved_slots
    assert "os" not in spec.resolved_slots


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_skip_retrieval_greetings(mock_get_gateway):
    """Greetings/social need NO retrieval - LLM returns skip_retrieval."""
    for q in ["hello", "hello]", "hi", "hey!", "thanks", "ok", "bye"]:
        mock_gateway = MagicMock()
        mock_gateway.chat = AsyncMock(
            return_value=_mock_llm_response({
                "canonical_query_en": q,
                "intent": "social",
                "entities": [],
                "required_evidence": [],
                "risk_level": "low",
                "is_ambiguous": False,
                "clarifying_questions": [],
                "retrieval_rewrites": [],
                "skip_retrieval": True,
                "canned_response": "Hello! Welcome. How can I help you today?",
            })
        )
        mock_get_gateway.return_value = mock_gateway

        spec = await normalize(q)
        assert spec.skip_retrieval, f"Expected skip_retrieval for {q!r}"
        assert spec.canned_response
        assert spec.intent == "social"


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_no_skip_for_questions(mock_get_gateway):
    """Actual questions need retrieval."""
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(
        return_value=_mock_llm_response({
            "canonical_query_en": "what is the price of VPS?",
            "intent": "transactional",
            "entities": ["vps"],
            "required_evidence": ["numbers_units", "transaction_link"],
            "risk_level": "low",
            "is_ambiguous": False,
            "clarifying_questions": [],
            "retrieval_rewrites": ["VPS pricing"],
            "skip_retrieval": False,
        })
    )
    mock_get_gateway.return_value = mock_gateway

    spec = await normalize("what is the price of VPS?")
    assert not spec.skip_retrieval


@patch("app.services.normalizer.get_llm_gateway")
@pytest.mark.asyncio
async def test_normalize_llm_fallback_on_error(mock_get_gateway):
    """When LLM fails, return minimal fallback QuerySpec."""
    mock_gateway = MagicMock()
    mock_gateway.chat = AsyncMock(side_effect=Exception("LLM timeout"))

    mock_get_gateway.return_value = mock_gateway

    spec = await normalize("some query")

    assert spec.extraction_mode == "llm_fallback"
    assert spec.intent == "informational"
    assert spec.retrieval_profile == "generic_profile"
    assert spec.keyword_queries == ["some query"]
    assert spec.semantic_queries == ["some query"]
