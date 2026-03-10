"""Tests for Phase 3 Decision Router."""

import pytest

from app.search.base import EvidenceChunk
from app.services.decision_router import route
from app.services.evidence_quality import QualityReport
from app.services.schemas import DecisionResult, QuerySpec


def _ambiguous_spec() -> QuerySpec:
    return QuerySpec(
        intent="ambiguous",
        entities=[],
        constraints={},
        required_evidence=[],
        risk_level="low",
        keyword_queries=["x"],
        semantic_queries=["x"],
        clarifying_questions=["What would you like to compare?"],
        is_ambiguous=True,
        answerable_without_clarification=False,
        blocking_clarifying_questions=["What would you like to compare?"],
    )


def test_route_ambiguous():
    dr = route(_ambiguous_spec(), None, [], [], True)
    assert dr.decision == "ASK_USER"
    assert dr.reason == "ambiguous_query"
    assert dr.lane == "ASK_USER"
    assert dr.answer
    assert dr.clarifying_questions


def test_route_pass():
    spec = QuerySpec(
        intent="informational",
        entities=[],
        constraints={},
        required_evidence=[],
        risk_level="low",
        keyword_queries=["x"],
        semantic_queries=["x"],
        clarifying_questions=[],
        is_ambiguous=False,
    )
    report = QualityReport(0.8, {"numbers_units": 0.9}, [], None, None)
    dr = route(spec, report, [], [], True)
    assert dr.decision == "PASS"
    assert dr.reason == "sufficient"
    assert dr.lane == "PASS_STRONG"
    assert dr.answer_policy == "direct"


def test_route_pass_weak_for_answerable_refinement_case():
    spec = QuerySpec(
        intent="transactional",
        entities=[],
        constraints={},
        required_evidence=["numbers_units"],
        risk_level="low",
        keyword_queries=["x"],
        semantic_queries=["x"],
        clarifying_questions=["What budget range do you have in mind?"],
        is_ambiguous=True,
        answerable_without_clarification=True,
        assistant_should_lead=True,
        missing_info_for_refinement=["budget"],
        refinement_questions=["What budget range do you have in mind?"],
        answer_mode_hint="weak",
    )
    report = QualityReport(0.8, {"numbers_units": 0.9}, [], None, None)
    evidence = [
        EvidenceChunk("c1", "Starter plan: $10/month", "https://example.com/pricing", "pricing", 0.8, "Starter plan: $10/month"),
    ]

    dr = route(spec, report, evidence, ["numbers_units"], True)

    assert dr.decision == "PASS"
    assert dr.reason == "answerable_with_refinement"
    assert dr.lane == "PASS_WEAK"
    assert dr.answer_policy == "bounded"
    assert dr.clarifying_questions == ["What budget range do you have in mind?"]


def test_route_missing_evidence_quality():
    spec = QuerySpec(
        intent="transactional",
        entities=[],
        constraints={},
        required_evidence=["numbers_units"],
        risk_level="low",
        keyword_queries=["x"],
        semantic_queries=["x"],
        clarifying_questions=[],
        is_ambiguous=False,
    )
    report = QualityReport(0.3, {"numbers_units": 0.1}, ["missing_numbers"], None, None)
    evidence = [
        EvidenceChunk("c1", "snippet", "https://example.com/page", "pricing", 0.8, "full"),
    ]
    dr = route(spec, report, evidence, ["numbers_units"], False)
    assert dr.decision == "ASK_USER"
    assert dr.reason == "missing_evidence_quality"
    assert dr.lane == "ASK_USER"
    assert dr.partial_links
    assert "https://example.com/page" in dr.partial_links


def test_route_asks_user_when_quality_gate_fails_even_with_partial_coverage():
    spec = QuerySpec(
        intent="transactional",
        entities=[],
        constraints={},
        required_evidence=["numbers_units", "has_any_url"],
        risk_level="low",
        keyword_queries=["x"],
        semantic_queries=["x"],
        clarifying_questions=[],
        is_ambiguous=False,
        hard_requirements=["numbers_units"],
    )
    report = QualityReport(
        0.35,
        {"numbers_units": 0.1, "has_any_url": 0.0},
        ["missing_links"],
        None,
        None,
        sufficiency_scores={"numbers_units": 1.0, "has_any_url": 0.0},
        hard_requirement_coverage={"numbers_units": True},
    )
    evidence = [
        EvidenceChunk("c1", "Price: $10/month", "https://example.com/pricing", "pricing", 0.8, "Price: $10/month"),
    ]

    dr = route(spec, report, evidence, ["numbers_units", "has_any_url"], False)

    assert dr.decision == "ASK_USER"
    assert dr.reason == "missing_evidence_quality"
    assert dr.lane == "ASK_USER"
    assert dr.answer_policy == "clarify"


def test_route_high_risk_insufficient():
    spec = QuerySpec(
        intent="policy",
        entities=[],
        constraints={},
        required_evidence=["policy_language"],
        risk_level="high",
        keyword_queries=["x"],
        semantic_queries=["x"],
        clarifying_questions=[],
        is_ambiguous=False,
    )
    report = QualityReport(0.2, {"policy_language": 0.1}, ["missing_policy"], None, None)
    dr = route(spec, report, [], ["policy_language"], False)
    assert dr.decision == "ESCALATE"
    assert dr.reason == "high_risk_insufficient"
    assert dr.lane == "ESCALATE"
