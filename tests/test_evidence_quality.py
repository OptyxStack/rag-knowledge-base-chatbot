"""Tests for Evidence Quality Gate and Evidence Hygiene."""

import pytest

from app.search.base import EvidenceChunk
from app.services.evidence_hygiene import compute_hygiene
from app.services.evidence_quality import (
    evaluate_quality,
    passes_quality_gate,
    QualityReport,
)
from app.services.retry_planner import plan_retry, RetryStrategy
from app.services.schemas import HypothesisSpec, QuerySpec


def test_compute_hygiene_empty():
    sigs = compute_hygiene([])
    assert sigs.chunk_count == 0
    assert sigs.pct_chunks_with_url == 0.0
    assert sigs.median_content_density == 0.0


def test_compute_hygiene_with_chunks():
    chunks = [
        EvidenceChunk("c1", "Plan: $10/mo at https://example.com/order", "https://example.com", "pricing", 0.9, "Plan: $10/mo at https://example.com/order"),
        EvidenceChunk("c2", "Contact us. Copyright 2024.", "https://example.com/menu", "nav", 0.5, "Contact us. Copyright 2024."),
    ]
    sigs = compute_hygiene(chunks)
    assert sigs.chunk_count == 2
    assert sigs.pct_chunks_with_url >= 50
    assert sigs.pct_chunks_with_number_unit >= 50
    assert sigs.pct_chunks_boilerplate_gt_06 >= 0


@pytest.mark.asyncio
async def test_evaluate_quality_empty():
    """Empty chunks returns fail report without LLM call."""
    report = await evaluate_quality("test query", [], ["numbers_units"], hard_requirements=["numbers_units"])
    assert report.quality_score == 0.0
    assert report.gate_pass is False
    assert "missing_evidence" in report.missing_signals or report.missing_signals
    assert report.hard_requirement_coverage == {"numbers_units": False}


def test_passes_quality_gate_no_required():
    report = QualityReport(0.7, {"numbers_units": 0.5}, [], None, 0.1)
    assert passes_quality_gate(report, None)


def test_passes_quality_gate_uses_gate_pass_when_set():
    """When report.gate_pass is set, passes_quality_gate uses it directly."""
    report_pass = QualityReport(0.3, {}, ["missing_numbers"], None, 0.5, gate_pass=True)
    assert passes_quality_gate(report_pass, ["numbers_units"], hard_requirements=["numbers_units"])

    report_fail = QualityReport(0.8, {"numbers_units": 0.9}, [], None, 0.1, gate_pass=False)
    assert not passes_quality_gate(report_fail, None)


def test_passes_quality_gate_uses_hard_coverage_when_gate_pass_none():
    """When gate_pass is None, falls back to hard_coverage check."""
    report = QualityReport(
        0.5, {}, [], None, 0.1,
        gate_pass=None,
        hard_requirement_coverage={"numbers_units": True},
    )
    assert passes_quality_gate(report, ["numbers_units"], hard_requirements=["numbers_units"])


def test_plan_retry_attempt_1():
    assert plan_retry(["missing_numbers"], 1) is None


def test_plan_retry_attempt_2():
    """With evidence_eval retry_needed, returns strategy from LLM output."""
    from app.services.evidence_evaluator import EvidenceEvalResult

    evidence_eval = EvidenceEvalResult(
        relevance_score=0.3,
        coverage_gaps=["missing pricing"],
        retry_needed=True,
        suggested_query="VPS monthly pricing USD",
        retry_boost_terms=["USD", "pricing"],
        retry_doc_types=["pricing"],
    )
    strat = plan_retry(["missing_numbers"], 2, evidence_eval_result=evidence_eval)
    assert strat is not None
    assert isinstance(strat, RetryStrategy)
    assert strat.suggested_query == "VPS monthly pricing USD"
    assert "USD" in strat.boost_patterns
    assert "pricing" in (strat.filter_doc_types or [])


def test_plan_retry_fallback_rewrite_candidates():
    """With query_spec rewrite_candidates, returns strategy when no evidence_eval."""
    from app.services.schemas import QuerySpec

    spec = QuerySpec(
        intent="policy",
        entities=[],
        constraints={},
        required_evidence=[],
        risk_level="low",
        keyword_queries=[],
        semantic_queries=[],
        clarifying_questions=[],
        is_ambiguous=False,
        rewrite_candidates=["refund policy", "refund terms for proxies"],
    )
    strat = plan_retry(["missing_policy"], 2, query_spec=spec)
    assert strat is not None
    assert strat.suggested_query == "refund terms for proxies"


def test_plan_retry_no_strategy_when_no_input():
    """Returns None when no evidence_eval and no rewrite_candidates."""
    strat = plan_retry(["missing_numbers"], 2)
    assert strat is None


def test_plan_retry_switches_to_fallback_hypothesis():
    spec = QuerySpec(
        intent="transactional",
        entities=[],
        constraints={},
        required_evidence=["numbers_units"],
        risk_level="low",
        keyword_queries=[],
        semantic_queries=[],
        clarifying_questions=[],
        is_ambiguous=False,
        fallback_hypotheses=[
            HypothesisSpec(
                name="fallback_policy",
                evidence_families=["policy_terms"],
                answer_shape="bounded_summary",
                retrieval_profile="policy_profile",
                required_evidence=["policy_language"],
                hard_requirements=[],
                soft_requirements=["numbers_units"],
                preferred_sources=["conversation"],
                query_hint="terms of service additional IPs for KVM VPS",
            )
        ],
    )
    strat = plan_retry(["missing_evidence"], 2, query_spec=spec)
    assert strat is not None
    assert strat.hypothesis_index == 1
    assert strat.hypothesis_name == "fallback_policy"
    assert strat.required_evidence_override == ["policy_language"]
    assert strat.preferred_sources_override == ["conversation"]
