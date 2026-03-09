"""Tests for retrieval planner (Workstream 3)."""

import pytest

from app.services.retrieval_planner import build_retrieval_plan
from app.services.schemas import QuerySpec


def test_build_retrieval_plan_attempt_1():
    """Attempt 1 produces broad_hybrid plan."""
    plan = build_retrieval_plan("vps pricing", 1)
    assert plan.profile == "generic_profile"
    assert plan.attempt_index == 1
    assert plan.reason == "broad_hybrid"
    assert plan.query_keyword
    assert plan.query_semantic
    assert plan.fetch_n > 0
    assert plan.rerank_k > 0


def test_build_retrieval_plan_prefers_query_spec_profile():
    """QuerySpec retrieval_profile overrides keyword heuristics."""
    spec = QuerySpec(
        intent="policy",
        entities=[],
        constraints={},
        required_evidence=[],
        risk_level="low",
        keyword_queries=["refund policy"],
        semantic_queries=["refund policy"],
        clarifying_questions=[],
        is_ambiguous=False,
        retrieval_profile="policy_profile",
    )
    plan = build_retrieval_plan("vps plans", 1, query_spec=spec)
    assert plan.profile == "policy_profile"


def test_build_retrieval_plan_attempt_2_with_retry_strategy():
    """Attempt 2 with retry strategy produces retry_precision plan."""
    from app.services.retry_planner import RetryStrategy

    plan = build_retrieval_plan(
        "pricing",
        2,
        retry_strategy=RetryStrategy(boost_patterns=["USD", "order"]),
    )
    assert plan.attempt_index == 2
    assert plan.reason == "retry_boost_patterns"
    assert plan.boost_patterns
    assert "USD" in plan.boost_patterns


def test_build_retrieval_plan_pricing_profile_increases_fetch():
    """Pricing profile increases fetch_n and rerank_k."""
    spec = QuerySpec(
        intent="transactional",
        entities=[],
        constraints={},
        required_evidence=["transaction_link"],
        risk_level="low",
        keyword_queries=["vps plans link"],
        semantic_queries=["vps plans link"],
        clarifying_questions=[],
        is_ambiguous=False,
        retrieval_profile="pricing_profile",
    )
    plan = build_retrieval_plan("vps plans", 1, query_spec=spec)
    assert plan.profile == "pricing_profile"
    assert plan.fetch_n >= 50
    assert plan.rerank_k >= 8


def test_build_retrieval_plan_uses_doc_type_prior_and_budget_hint():
    spec = QuerySpec(
        intent="policy",
        entities=[],
        constraints={},
        required_evidence=["policy_language"],
        risk_level="high",
        keyword_queries=["refund policy"],
        semantic_queries=["refund policy"],
        clarifying_questions=[],
        is_ambiguous=False,
        retrieval_profile="policy_profile",
        hard_requirements=["policy_language"],
        doc_type_prior=["policy", "tos"],
    )
    plan = build_retrieval_plan("refund policy", 1, query_spec=spec)
    assert plan.preferred_doc_types is not None
    assert plan.preferred_doc_types[:2] == ["policy", "tos"]
    assert plan.budget_hint is not None
    assert plan.budget_hint.get("boost_pricing") is False
    assert "policy_language" in (plan.budget_hint.get("hard_requirements") or [])
    assert "policy" in (plan.budget_hint.get("ensure_doc_types") or [])


def test_build_retrieval_plan_does_not_infer_doc_types_when_queryspec_present():
    spec = QuerySpec(
        intent="policy",
        entities=[],
        constraints={},
        required_evidence=["policy_language"],
        risk_level="high",
        keyword_queries=["refund policy"],
        semantic_queries=["refund policy"],
        clarifying_questions=[],
        is_ambiguous=False,
        retrieval_profile="policy_profile",
        hard_requirements=["policy_language"],
        doc_type_prior=[],
    )

    plan = build_retrieval_plan("refund policy", 1, query_spec=spec)

    assert plan.preferred_doc_types is None
    assert plan.profile == "policy_profile"
