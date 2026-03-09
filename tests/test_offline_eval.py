"""Tests for Phase 4 offline evaluation harness."""

from pathlib import Path

import pytest

from app.services.offline_eval import (
    OfflineEvalCase,
    evaluate_case,
    load_eval_cases_jsonl,
    run_offline_eval,
)
from app.services.schemas import AnswerOutput


def _debug_payload(
    *,
    evidence_ids: list[str],
    covered_requirements: list[str] | None = None,
    hard_coverage: dict[str, bool] | None = None,
    unsupported_claims: list[str] | None = None,
):
    return {
        "evidence_summary": [{"chunk_id": cid} for cid in evidence_ids],
        "evidence_set": {"covered_requirements": covered_requirements or []},
        "quality_report": {"hard_requirement_coverage": hard_coverage or {}},
        "review_unsupported_claims": unsupported_claims or [],
    }


class _FakeAnswerService:
    def __init__(self, outputs_by_query: dict[str, AnswerOutput]):
        self._outputs_by_query = outputs_by_query

    async def generate(self, query: str, conversation_history=None, trace_id=None):
        _ = (conversation_history, trace_id)
        return self._outputs_by_query[query]


def test_load_eval_cases_contains_required_replay_classes():
    fixture = Path(__file__).parent / "fixtures" / "offline_eval_replay_cases.jsonl"
    cases = load_eval_cases_jsonl(fixture)
    tags = {tag for case in cases for tag in case.tags}

    assert "ambiguous_referent" in tags
    assert "policy_question" in tags
    assert "pricing_question" in tags
    assert "troubleshooting_steps" in tags
    assert "multilingual_query" in tags


@pytest.mark.asyncio
async def test_evaluate_case_computes_split_metrics_and_passes():
    case = OfflineEvalCase(
        name="policy_case",
        input="what is refund policy?",
        expected_decision="PASS",
        expected_chunk_ids=["chunk-policy-1"],
        required_evidence=["policy_language"],
        expected_answer_contains=["refund within 30 days"],
    )
    output = AnswerOutput(
        decision="PASS",
        answer="Our refund policy allows refund within 30 days.",
        followup_questions=[],
        citations=[{"chunk_id": "chunk-policy-1"}],
        confidence=0.9,
        debug=_debug_payload(
            evidence_ids=["chunk-policy-1", "chunk-faq-1"],
            covered_requirements=["policy_language"],
            hard_coverage={"policy_language": True},
            unsupported_claims=[],
        ),
    )
    svc = _FakeAnswerService({case.input: output})

    result = await evaluate_case(svc, case, run_id="testrun")

    assert result.passed is True
    assert result.metrics["retrieval_recall"] == 1.0
    assert result.metrics["evidence_coverage"] == 1.0
    assert result.metrics["answer_correctness"] == 1.0
    assert result.metrics["hallucination_rate"] == 0.0
    assert result.metrics["citation_validity"] == 1.0


@pytest.mark.asyncio
async def test_run_offline_eval_marks_hallucination_failures():
    case = OfflineEvalCase(
        name="pricing_case",
        input="price?",
        expected_decision="PASS",
        expected_chunk_ids=["chunk-price-1"],
        required_evidence=["numbers_units"],
        expected_answer_contains=["$10"],
        forbidden_answer_contains=["free forever"],
        hallucination_threshold=0.0,
    )
    output = AnswerOutput(
        decision="PASS",
        answer="Price is $10 monthly and free forever for premium users.",
        followup_questions=[],
        citations=[{"chunk_id": "chunk-price-1"}],
        confidence=0.8,
        debug=_debug_payload(
            evidence_ids=["chunk-price-1"],
            covered_requirements=["numbers_units"],
            hard_coverage={"numbers_units": True},
            unsupported_claims=["free forever for premium users"],
        ),
    )
    svc = _FakeAnswerService({case.input: output})

    summary, results = await run_offline_eval(svc, [case], run_id="testrun2")

    assert summary.case_count == 1
    assert summary.fail_count == 1
    assert results[0].passed is False
    assert results[0].metrics["hallucination_rate"] > 0.0
    assert results[0].metrics["forbidden_violations"]
