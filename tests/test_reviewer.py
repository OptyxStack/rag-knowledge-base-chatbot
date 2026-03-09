"""Tests for reviewer gate."""

import pytest

from app.services.reviewer import ReviewerGate, ReviewerStatus


def test_reviewer_pass_with_citations(mock_evidence_chunks):
    """PASS with valid citations should pass reviewer."""
    gate = ReviewerGate()
    result = gate.review(
        decision="PASS",
        answer="You can get a refund within 30 days.",
        citations=[
            {"chunk_id": "chunk-1", "source_url": "https://example.com/refund", "doc_type": "policy"},
        ],
        evidence=mock_evidence_chunks,
        query="refund policy",
        confidence=0.9,
    )
    assert result.status == ReviewerStatus.PASS
    assert not result.reasons


def test_reviewer_fail_pass_without_citations(mock_evidence_chunks):
    """PASS without citations should fail to ASK_USER."""
    gate = ReviewerGate()
    result = gate.review(
        decision="PASS",
        answer="You can get a refund within 30 days.",
        citations=[],
        evidence=mock_evidence_chunks,
        query="refund policy",
        confidence=0.9,
    )
    assert result.status == ReviewerStatus.ASK_USER
    assert "citation" in result.reasons[0].lower()


def test_reviewer_fail_citation_not_in_evidence(mock_evidence_chunks):
    """Citation with chunk_id not in evidence should fail."""
    gate = ReviewerGate()
    result = gate.review(
        decision="PASS",
        answer="Refunds available.",
        citations=[{"chunk_id": "chunk-999", "source_url": "...", "doc_type": "policy"}],
        evidence=mock_evidence_chunks,
        query="refund",
        confidence=0.8,
    )
    assert result.status == ReviewerStatus.ASK_USER
    assert "not in evidence" in result.reasons[0].lower()


def test_reviewer_high_risk_requires_policy(mock_evidence_chunks):
    """High-risk query (refund) without policy citation should ESCALATE."""
    gate = ReviewerGate(require_policy_for_high_risk=True)
    result = gate.review(
        decision="PASS",
        answer="You may get a refund.",
        citations=[
            {"chunk_id": "chunk-2", "source_url": "https://example.com/billing", "doc_type": "faq"},
        ],
        evidence=mock_evidence_chunks,
        query="I want a refund",
        confidence=0.9,
    )
    assert result.status == ReviewerStatus.ESCALATE
    assert "policy" in result.reasons[0].lower()


def test_reviewer_legacy_retrieve_more_input_becomes_ask_user(mock_evidence_chunks):
    """Legacy RETRIEVE_MORE input should now default to ASK_USER."""
    gate = ReviewerGate()
    result = gate.review(
        decision="RETRIEVE_MORE",
        answer="Some answer.",
        citations=[],
        evidence=mock_evidence_chunks,
        query="test",
        confidence=0.5,
        retrieval_attempt=2,
        max_attempts=2,
    )
    assert result.status == ReviewerStatus.ASK_USER
    assert "defaulting to ASK_USER" in result.reasons[0]


def test_reviewer_allows_bounded_pass_weak_with_single_citation(mock_evidence_chunks):
    """Bounded PASS_WEAK answers should not be forced into RETRIEVE_MORE by 2-citation heuristics."""
    gate = ReviewerGate()
    result = gate.review(
        decision="PASS",
        answer="The available evidence confirms one pricing detail: $10/month. I could not verify the full pricing table.",
        citations=[
            {"chunk_id": "chunk-2", "source_url": "https://example.com/billing", "doc_type": "faq"},
        ],
        evidence=mock_evidence_chunks,
        query="What is the price?",
        confidence=0.6,
        answer_policy="bounded",
        lane="PASS_WEAK",
    )
    assert result.status == ReviewerStatus.PASS
    assert not result.reasons


def test_reviewer_trim_unsupported_claims_when_mixed(mock_evidence_chunks):
    """Workstream 5: Trim unsupported ($100) but keep policy claim (30 days) when policy citation exists."""
    gate = ReviewerGate()
    result = gate.review(
        decision="PASS",
        answer="You can contact support for help. The price is $100 per month. Our policy says refunds within 30 days.",
        citations=[
            {"chunk_id": "chunk-1", "source_url": "https://example.com/refund", "doc_type": "policy"},
        ],
        evidence=mock_evidence_chunks,
        query="pricing and support",
        confidence=0.7,
    )
    assert result.status == ReviewerStatus.TRIM_UNSUPPORTED
    assert result.trimmed_answer
    assert "$100" not in result.trimmed_answer
    assert "30 days" in result.trimmed_answer  # policy claim kept (supported by policy citation)
    assert result.unsupported_claims


def test_reviewer_downgrade_lane_when_bounded_and_low_coverage(mock_evidence_chunks):
    """Workstream 5: Bounded answer with citation can downgrade instead of RETRIEVE_MORE."""
    gate = ReviewerGate(min_citation_coverage=0.9)
    result = gate.review(
        decision="PASS",
        answer="Based on available evidence, support is available. I could not verify all details.",
        citations=[
            {"chunk_id": "chunk-2", "source_url": "https://example.com/billing", "doc_type": "faq"},
        ],
        evidence=mock_evidence_chunks,
        query="support",
        confidence=0.6,
        answer_policy="bounded",
        lane="PASS_WEAK",
    )
    assert result.status in (ReviewerStatus.PASS, ReviewerStatus.DOWNGRADE_LANE)
