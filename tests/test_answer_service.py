"""Tests for answer service helpers."""

from app.services.answer_utils import (
    apply_answer_plan,
    build_answer_plan,
    collect_rewrite_candidates,
    resolve_retrieval_query,
)
from app.services.evidence_quality import QualityReport
from app.services.retry_planner import RetryStrategy
from app.services.schemas import DecisionResult, QuerySpec


def test_collect_rewrite_candidates_dedupes_case_insensitive():
    spec = QuerySpec(
        intent="transactional",
        entities=[],
        constraints={},
        required_evidence=[],
        risk_level="low",
        keyword_queries=[],
        semantic_queries=[],
        clarifying_questions=[],
        is_ambiguous=False,
        rewrite_candidates=["Pricing Query", "pricing query", "Dedicated pricing"],
    )

    candidates = collect_rewrite_candidates("pricing query", spec)

    assert candidates == ["pricing query", "Dedicated pricing"]


def test_resolve_retrieval_query_uses_rewrite_candidate_on_retry():
    spec = QuerySpec(
        intent="transactional",
        entities=[],
        constraints={},
        required_evidence=[],
        risk_level="low",
        keyword_queries=[],
        semantic_queries=[],
        clarifying_questions=[],
        is_ambiguous=False,
        rewrite_candidates=["pricing query", "dedicated monthly pricing"],
    )

    query, source, candidates = resolve_retrieval_query(
        base_query="pricing query",
        attempt=2,
        query_spec=spec,
        retry_strategy=None,
    )

    assert query == "dedicated monthly pricing"
    assert source == "rewrite_candidate_1"
    assert candidates == ["pricing query", "dedicated monthly pricing"]


def test_resolve_retrieval_query_prefers_retry_strategy_suggestion():
    spec = QuerySpec(
        intent="transactional",
        entities=[],
        constraints={},
        required_evidence=[],
        risk_level="low",
        keyword_queries=[],
        semantic_queries=[],
        clarifying_questions=[],
        is_ambiguous=False,
        rewrite_candidates=["pricing query", "dedicated monthly pricing"],
    )
    strategy = RetryStrategy(suggested_query="policy refund terms")

    query, source, _ = resolve_retrieval_query(
        base_query="pricing query",
        attempt=2,
        query_spec=spec,
        retry_strategy=strategy,
        explicit_override="reviewer suggested query",
    )

    assert query == "policy refund terms"
    assert source == "retry_strategy_suggested_query"


def test_build_answer_plan_for_pass_weak_lane():
    spec = QuerySpec(
        intent="transactional",
        entities=[],
        constraints={},
        required_evidence=["has_any_url"],
        risk_level="low",
        keyword_queries=[],
        semantic_queries=[],
        clarifying_questions=[],
        is_ambiguous=False,
        hard_requirements=["numbers_units"],
    )
    dr = DecisionResult(
        decision="PASS",
        reason="partial_sufficient",
        clarifying_questions=[],
        partial_links=[],
        answer_policy="bounded",
        lane="PASS_WEAK",
    )
    report = QualityReport(
        quality_score=0.35,
        feature_scores={"numbers_units": 0.1},
        missing_signals=["missing_links"],
        staleness_risk=None,
        boilerplate_risk=0.0,
    )

    plan = build_answer_plan(dr, spec, report)

    assert plan.lane == "PASS_WEAK"
    assert plan.allowed_claim_scope == "partial"
    assert plan.tone_policy == "cautious"
    assert "numbers_units" in plan.required_citations


def test_apply_answer_plan_bounds_pass_weak_output():
    plan = build_answer_plan(
        DecisionResult(
            decision="PASS",
            reason="partial_sufficient",
            clarifying_questions=["Which region do you prefer?"],
            partial_links=[],
            answer_policy="bounded",
            lane="PASS_WEAK",
        ),
        None,
        None,
    )

    decision, answer, followup, confidence = apply_answer_plan(
        plan,
        {
            "decision": "ASK_USER",
            "answer": "The available evidence shows the service starts at $10/month.",
            "followup_questions": ["Which plan do you want?"],
            "confidence": 0.92,
        },
    )

    assert decision == "PASS"
    assert followup == ["Which plan do you want?"]
    assert confidence == 0.6
    assert "still unverified" in answer.lower()


def test_apply_answer_plan_uses_router_followup_for_pass_weak_when_llm_omits_it():
    plan = build_answer_plan(
        DecisionResult(
            decision="PASS",
            reason="answerable_with_refinement",
            clarifying_questions=["What budget range works for you?"],
            partial_links=[],
            answer_policy="bounded",
            lane="PASS_WEAK",
        ),
        None,
        None,
    )

    decision, answer, followup, confidence = apply_answer_plan(
        plan,
        {
            "decision": "PASS",
            "answer": "A good starting point is 4 GB RAM and 2 vCPU.",
            "followup_questions": [],
            "confidence": 0.8,
        },
    )

    assert decision == "PASS"
    assert "starting point" in answer
    assert followup == ["What budget range works for you?"]
    assert confidence == 0.6
