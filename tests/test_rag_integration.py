"""Integration tests for full RAG flow end-to-end.

Tests the complete pipeline: Understand → Retrieve → Assess → Decide → Generate → Verify
with mocked external services (OpenSearch, Qdrant, LLM). Validates orchestration logic
and phase transitions.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.search.base import EvidenceChunk
from app.services.answer_service import AnswerService
from app.services.retrieval import EvidencePack
from app.services.reviewer import ReviewerGate, ReviewerResult, ReviewerStatus
from app.services.schemas import QuerySpec


# --- Mock fixtures ---


def _make_query_spec(
    intent: str = "transactional",
    required_evidence: list[str] | None = None,
    keyword_queries: list[str] | None = None,
    semantic_queries: list[str] | None = None,
    is_ambiguous: bool = False,
    clarifying_questions: list[str] | None = None,
    answerable_without_clarification: bool = True,
    assistant_should_lead: bool = False,
    refinement_questions: list[str] | None = None,
) -> QuerySpec:
    """Build QuerySpec for integration tests."""
    return QuerySpec(
        intent=intent,
        entities=["vps"],
        constraints={},
        required_evidence=required_evidence or ["numbers_units", "has_any_url"],
        risk_level="low",
        keyword_queries=keyword_queries or ["VPS pricing", "VPS price"],
        semantic_queries=semantic_queries or ["VPS pricing plans"],
        clarifying_questions=clarifying_questions or [],
        is_ambiguous=is_ambiguous,
        answerable_without_clarification=answerable_without_clarification,
        assistant_should_lead=assistant_should_lead,
        refinement_questions=refinement_questions or [],
        skip_retrieval=False,
        canonical_query_en="What is the VPS pricing?",
        hard_requirements=["numbers_units", "has_any_url"],
        retrieval_profile="pricing_profile",
    )


def _make_evidence_chunks() -> list[EvidenceChunk]:
    """Evidence chunks that pass rule-based quality gate (numbers + URL)."""
    return [
        EvidenceChunk(
            chunk_id="chunk-1",
            snippet="VPS plans start at $6/month. Order at https://green.cloud/order/vps.",
            source_url="https://green.cloud/pricing",
            doc_type="pricing",
            score=0.92,
            full_text="VPS plans start at $6/month. Order at https://green.cloud/order/vps.",
        ),
        EvidenceChunk(
            chunk_id="chunk-2",
            snippet="Dedicated servers from $110/month. See https://green.cloud/dedicated.",
            source_url="https://green.cloud/dedicated",
            doc_type="pricing",
            score=0.85,
            full_text="Dedicated servers from $110/month. See https://green.cloud/dedicated.",
        ),
    ]


def _make_mixed_evidence_chunks() -> list[EvidenceChunk]:
    return [
        EvidenceChunk(
            chunk_id="chunk-price-1",
            snippet="VPS plans start at $6/month.",
            source_url="https://green.cloud/pricing",
            doc_type="pricing",
            score=0.92,
            full_text="VPS plans start at $6/month.",
        ),
        EvidenceChunk(
            chunk_id="chunk-conv-1",
            snippet="Conversation example: customer requested an extra IP and support added it after verification.",
            source_url="ticket://conversation-1",
            doc_type="conversation",
            score=0.87,
            full_text="Conversation example: customer requested an extra IP and support added it after verification.",
        ),
    ]


def _make_llm_response(
    decision: str = "PASS",
    answer: str = "VPS plans start at $6/month. Order at https://green.cloud/order/vps.",
    citations: list[dict] | None = None,
    confidence: float = 0.9,
) -> MagicMock:
    """Build mock LLM response with JSON content."""
    content = json.dumps({
        "decision": decision,
        "answer": answer,
        "followup_questions": [],
        "citations": citations or [
            {"chunk_id": "chunk-1", "source_url": "https://green.cloud/pricing", "doc_type": "pricing"},
        ],
        "confidence": confidence,
    })
    resp = MagicMock()
    resp.content = content
    resp.finish_reason = "stop"
    resp.input_tokens = 100
    resp.output_tokens = 50
    return resp


class MockRetrievalService:
    """Mock retrieval that returns predefined evidence."""

    def __init__(self, chunks: list[EvidenceChunk] | None = None):
        self.chunks = _make_evidence_chunks() if chunks is None else chunks

    async def retrieve(self, *args, **kwargs) -> EvidencePack:
        return EvidencePack(
            chunks=self.chunks,
            retrieval_stats={
                "bm25_count": len(self.chunks),
                "vector_count": len(self.chunks),
                "merged_count": len(self.chunks),
                "reranked_count": len(self.chunks),
            },
        )


class MockLLMGateway:
    """Mock LLM gateway. Returns evidence-quality format for assess phase, generate format for generate phase."""

    def __init__(self, response: MagicMock | None = None):
        self._response = response or _make_llm_response()

    async def chat(self, messages=None, **kwargs) -> MagicMock:
        if messages and any("evaluate whether retrieved evidence" in str(m.get("content", "")) for m in messages if isinstance(m, dict)):
            resp = MagicMock()
            resp.content = json.dumps({
                "pass": True,
                "confidence": 0.9,
                "reason": "Evidence sufficient",
                "missing_signals": [],
                "coverage": {"numbers_units": True, "has_any_url": True},
            })
            resp.finish_reason = "stop"
            resp.input_tokens = 50
            resp.output_tokens = 30
            return resp
        return self._response


class MockReviewerGate:
    """Mock reviewer that always returns PASS."""

    def review(self, *args, **kwargs) -> ReviewerResult:
        return ReviewerResult(
            status=ReviewerStatus.PASS,
            reasons=[],
            suggested_queries=[],
            missing_fields=[],
        )


# --- Integration tests ---


@pytest.fixture(autouse=True)
def _disable_llm_phases(monkeypatch):
    """Disable LLM-based phases to avoid extra API calls in integration tests."""
    monkeypatch.setattr(
        "app.services.archi_config.get_evidence_evaluator_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.archi_config.get_decision_router_use_llm",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.archi_config.get_self_critic_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.archi_config.get_final_polish_enabled",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.archi_config.get_retrieval_doc_type_use_llm",
        lambda: False,
    )
    monkeypatch.setattr(
        "app.services.archi_config.get_language_detect_enabled",
        lambda: False,
    )


@patch("app.services.answer_service.match_intent")
@patch("app.services.answer_service.normalize_query")
@pytest.mark.asyncio
async def test_rag_flow_full_pipeline_pass(mock_normalize, mock_match_intent):
    """Full RAG flow: query → normalized → retrieve → assess → decide → generate → verify → DONE."""
    mock_match_intent.return_value = None
    mock_normalize.return_value = _make_query_spec()

    retrieval = MockRetrievalService()
    llm = MockLLMGateway()
    reviewer = MockReviewerGate()

    svc = AnswerService(
        retrieval=retrieval,
        llm=llm,
        reviewer=reviewer,
    )

    output = await svc.generate(
        query="What is the VPS pricing?",
        conversation_history=None,
        trace_id="test-trace-123",
    )

    assert output.decision == "PASS"
    assert len(output.answer) > 0
    assert "6" in output.answer or "pricing" in output.answer.lower()
    assert output.confidence > 0
    assert output.debug is not None
    assert "trace_id" in (output.debug or {})

    mock_normalize.assert_called_once()
    mock_match_intent.assert_called_once()


@patch("app.services.answer_service.match_intent")
@patch("app.services.answer_service.normalize_query")
@pytest.mark.asyncio
async def test_rag_flow_ask_user_when_no_evidence(mock_normalize, mock_match_intent):
    """RAG flow terminates with ASK_USER when retrieval returns no evidence."""
    mock_match_intent.return_value = None
    mock_normalize.return_value = _make_query_spec()

    retrieval = MockRetrievalService(chunks=[])
    llm = MockLLMGateway()
    reviewer = MockReviewerGate()

    svc = AnswerService(
        retrieval=retrieval,
        llm=llm,
        reviewer=reviewer,
    )

    output = await svc.generate(
        query="What is the VPS pricing?",
        conversation_history=None,
        trace_id="test-trace-no-evidence",
    )

    assert output.decision == "ASK_USER"
    assert len(output.answer) > 0
    mock_normalize.assert_called_once()


@patch("app.services.answer_service.match_intent")
@patch("app.services.answer_service.normalize_query")
@pytest.mark.asyncio
async def test_rag_flow_intent_cache_bypass(mock_normalize, mock_match_intent):
    """Intent cache hit bypasses retrieval and LLM."""
    from app.services.branding_config import IntentMatch

    mock_match_intent.return_value = IntentMatch(
        intent="hello",
        answer="Hello! How can I help you today?",
    )
    mock_normalize.return_value = _make_query_spec()

    retrieval = MockRetrievalService()
    llm = MockLLMGateway()
    reviewer = MockReviewerGate()

    svc = AnswerService(
        retrieval=retrieval,
        llm=llm,
        reviewer=reviewer,
    )

    output = await svc.generate(
        query="hi",
        conversation_history=None,
        trace_id="test-trace-intent",
    )

    assert output.decision == "PASS"
    assert "Hello" in output.answer
    assert output.confidence == 1.0
    mock_normalize.assert_not_called()


@patch("app.services.answer_service.match_intent")
@patch("app.services.answer_service.normalize_query")
@pytest.mark.asyncio
async def test_rag_flow_with_conversation_history(mock_normalize, mock_match_intent):
    """RAG flow uses conversation history for context."""
    mock_match_intent.return_value = None
    mock_normalize.return_value = _make_query_spec()

    retrieval = MockRetrievalService()
    llm = MockLLMGateway()
    reviewer = MockReviewerGate()

    svc = AnswerService(
        retrieval=retrieval,
        llm=llm,
        reviewer=reviewer,
    )

    output = await svc.generate(
        query="And the dedicated server price?",
        conversation_history=[
            {"role": "user", "content": "What VPS plans do you have?"},
            {"role": "assistant", "content": "We have VPS from $6/month..."},
        ],
        trace_id="test-trace-history",
    )

    assert output.decision == "PASS"
    mock_normalize.assert_called_once()
    # normalize(query, conversation_history, source_lang=...)
    call_args, call_kwargs = mock_normalize.call_args
    assert len(call_args) >= 2
    assert len(call_args[1]) == 2


@patch("app.services.answer_service.match_intent")
@patch("app.services.answer_service.normalize_query")
@pytest.mark.asyncio
async def test_rag_flow_ambiguous_returns_ask_user(mock_normalize, mock_match_intent):
    """Ambiguous query (is_ambiguous=True) returns ASK_USER with clarifying questions."""
    mock_match_intent.return_value = None
    mock_normalize.return_value = _make_query_spec(
        is_ambiguous=True,
        clarifying_questions=["What would you like to compare?"],
        answerable_without_clarification=False,
    )

    retrieval = MockRetrievalService()
    llm = MockLLMGateway()
    reviewer = MockReviewerGate()

    svc = AnswerService(
        retrieval=retrieval,
        llm=llm,
        reviewer=reviewer,
    )

    output = await svc.generate(
        query="what diff from this?",
        conversation_history=None,
        trace_id="test-trace-ambiguous",
    )

    assert output.decision == "ASK_USER"
    assert len(output.followup_questions) > 0


@patch("app.services.answer_service.match_intent")
@patch("app.services.answer_service.normalize_query")
@pytest.mark.asyncio
async def test_rag_flow_answer_then_ask_uses_pass_weak_followup(mock_normalize, mock_match_intent):
    mock_match_intent.return_value = None
    mock_normalize.return_value = _make_query_spec(
        is_ambiguous=True,
        clarifying_questions=["What budget range works for you?"],
        answerable_without_clarification=True,
        assistant_should_lead=True,
        refinement_questions=["What budget range works for you?"],
    )

    retrieval = MockRetrievalService()
    llm = MockLLMGateway(
        response=_make_llm_response(
            decision="PASS",
            answer="A good starting point is a VPS plan from $6/month.",
            confidence=0.85,
        )
    )
    reviewer = MockReviewerGate()

    svc = AnswerService(
        retrieval=retrieval,
        llm=llm,
        reviewer=reviewer,
    )

    output = await svc.generate(
        query="i want some vps for seo tools, can you suggest a plan?",
        conversation_history=None,
        trace_id="test-trace-answer-then-ask",
    )

    assert output.decision == "PASS"
    assert "starting point" in output.answer.lower()
    assert output.followup_questions == ["What budget range works for you?"]


@patch("app.services.answer_service.match_intent")
@patch("app.services.answer_service.normalize_query")
@pytest.mark.asyncio
async def test_rag_flow_supports_mixed_conversation_and_doc_evidence(mock_normalize, mock_match_intent):
    mock_match_intent.return_value = None
    mock_normalize.return_value = _make_query_spec(
        required_evidence=["numbers_units"],
        answerable_without_clarification=True,
    )

    retrieval = MockRetrievalService(chunks=_make_mixed_evidence_chunks())
    llm = MockLLMGateway(
        response=_make_llm_response(
            decision="PASS",
            answer="VPS plans start at $6/month, and a prior conversation example shows extra IP requests can be handled by support.",
            citations=[
                {"chunk_id": "chunk-price-1", "source_url": "https://green.cloud/pricing", "doc_type": "pricing"},
                {"chunk_id": "chunk-conv-1", "source_url": "ticket://conversation-1", "doc_type": "conversation"},
            ],
            confidence=0.82,
        )
    )
    reviewer = MockReviewerGate()

    svc = AnswerService(retrieval=retrieval, llm=llm, reviewer=reviewer)
    output = await svc.generate(
        query="can i buy more ip for my vps",
        conversation_history=None,
        trace_id="test-trace-mixed-evidence",
    )

    assert output.decision == "PASS"
    assert len(output.citations) == 2
    assert {c["doc_type"] for c in output.citations} == {"pricing", "conversation"}
