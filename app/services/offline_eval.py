"""Offline evaluation harness for replay-style RAG regression testing."""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import uuid4

from sqlalchemy import select

from app.core.logging import get_logger
from app.core.metrics import (
    offline_eval_answer_correctness,
    offline_eval_cases_total,
    offline_eval_evidence_coverage,
    offline_eval_hallucination_rate,
    offline_eval_retrieval_recall,
    offline_eval_runs_total,
)

logger = get_logger(__name__)


@dataclass
class OfflineEvalCase:
    """One offline eval case loaded from JSONL."""

    name: str
    input: str
    tags: list[str] = field(default_factory=list)
    conversation_history: list[dict[str, str]] = field(default_factory=list)
    expected_decision: str | None = None
    expected_chunk_ids: list[str] = field(default_factory=list)
    required_evidence: list[str] = field(default_factory=list)
    expected_answer_contains: list[str] = field(default_factory=list)
    forbidden_answer_contains: list[str] = field(default_factory=list)
    correctness_threshold: float = 0.7
    hallucination_threshold: float = 0.2
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class OfflineEvalCaseResult:
    """Computed metrics for one eval case."""

    run_id: str
    case_name: str
    input: str
    passed: bool
    output_decision: str
    output_confidence: float
    metrics: dict[str, Any]
    output_preview: str
    tags: list[str] = field(default_factory=list)


@dataclass
class OfflineEvalRunSummary:
    """Aggregate metrics for one run."""

    run_id: str
    case_count: int
    pass_count: int
    fail_count: int
    pass_rate: float
    retrieval_recall_avg: float | None
    evidence_coverage_avg: float | None
    answer_correctness_avg: float | None
    hallucination_rate_avg: float | None
    citation_validity_avg: float | None


def _to_str_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        out = [str(v).strip() for v in value if str(v).strip()]
    else:
        text = str(value).strip()
        out = [text] if text else []
    return list(dict.fromkeys(out))


def _to_history(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    out: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "")).strip()
        content = str(item.get("content", "")).strip()
        if role and content:
            out.append({"role": role, "content": content})
    return out


def load_eval_cases_jsonl(path: str | Path) -> list[OfflineEvalCase]:
    """Load offline eval cases from JSONL."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Eval dataset not found: {p}")

    cases: list[OfflineEvalCase] = []
    with p.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                obj = json.loads(text)
            except json.JSONDecodeError as e:
                raise ValueError(f"Invalid JSON on line {lineno} in {p}: {e}") from e
            if not isinstance(obj, dict):
                raise ValueError(f"Line {lineno} in {p} must be a JSON object")

            raw_input = str(obj.get("input") or obj.get("query") or "").strip()
            name = str(obj.get("name") or f"case_{lineno}").strip()
            if not raw_input:
                raise ValueError(f"Line {lineno} in {p} missing 'input' or 'query'")

            case = OfflineEvalCase(
                name=name,
                input=raw_input,
                tags=_to_str_list(obj.get("tags")),
                conversation_history=_to_history(obj.get("conversation_history")),
                expected_decision=str(obj.get("expected_decision")).strip().upper() or None,
                expected_chunk_ids=_to_str_list(obj.get("expected_chunk_ids")),
                required_evidence=_to_str_list(obj.get("required_evidence")),
                expected_answer_contains=_to_str_list(obj.get("expected_answer_contains")),
                forbidden_answer_contains=_to_str_list(obj.get("forbidden_answer_contains")),
                correctness_threshold=float(obj.get("correctness_threshold", 0.7)),
                hallucination_threshold=float(obj.get("hallucination_threshold", 0.2)),
                metadata=obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {},
            )
            cases.append(case)
    return cases


def _extract_debug_evidence_ids(debug: dict[str, Any]) -> set[str]:
    ids: set[str] = set()
    for row in debug.get("evidence_summary", []) or []:
        if isinstance(row, dict):
            chunk_id = str(row.get("chunk_id", "")).strip()
            if chunk_id:
                ids.add(chunk_id)
    return ids


def _retrieval_recall(case: OfflineEvalCase, debug: dict[str, Any]) -> tuple[float | None, list[str]]:
    expected = {cid for cid in case.expected_chunk_ids if cid}
    if not expected:
        return None, []
    actual = _extract_debug_evidence_ids(debug)
    matched = sorted(expected & actual)
    return len(matched) / max(1, len(expected)), matched


def _evidence_coverage(case: OfflineEvalCase, debug: dict[str, Any]) -> tuple[float | None, list[str]]:
    required = {req for req in case.required_evidence if req}
    if not required:
        return None, []

    covered: set[str] = set()
    evidence_set = debug.get("evidence_set") or {}
    covered_from_set = evidence_set.get("covered_requirements") if isinstance(evidence_set, dict) else []
    for req in _to_str_list(covered_from_set):
        covered.add(req)

    qr = debug.get("quality_report") or {}
    hard_coverage = qr.get("hard_requirement_coverage") if isinstance(qr, dict) else {}
    if isinstance(hard_coverage, dict):
        for req, ok in hard_coverage.items():
            if ok is True:
                covered.add(str(req))

    matched = sorted(required & covered)
    return len(matched) / max(1, len(required)), matched


def _answer_correctness(case: OfflineEvalCase, answer: str) -> tuple[float | None, list[str], list[str]]:
    expected = [s.lower() for s in case.expected_answer_contains if s]
    forbidden = [s.lower() for s in case.forbidden_answer_contains if s]
    if not expected and not forbidden:
        return None, [], []

    answer_l = (answer or "").lower()
    missing_expected = [s for s in expected if s not in answer_l]
    violated_forbidden = [s for s in forbidden if s in answer_l]

    expected_score = 1.0 if not expected else (len(expected) - len(missing_expected)) / len(expected)
    forbidden_penalty = 0.0 if not forbidden else len(violated_forbidden) / len(forbidden)
    score = max(0.0, min(1.0, expected_score - forbidden_penalty))
    return score, missing_expected, violated_forbidden


def _citation_validity(citations: list[dict[str, Any]], debug: dict[str, Any]) -> tuple[float | None, int, int]:
    if not citations:
        return None, 0, 0
    valid_ids = _extract_debug_evidence_ids(debug)
    valid = 0
    total = 0
    for c in citations:
        if not isinstance(c, dict):
            continue
        total += 1
        cid = str(c.get("chunk_id", "")).strip()
        if cid and cid in valid_ids:
            valid += 1
    if total == 0:
        return None, 0, 0
    return valid / total, valid, total


def _hallucination_rate(answer: str, debug: dict[str, Any]) -> tuple[float | None, int]:
    unsupported = debug.get("review_unsupported_claims") or []
    if not isinstance(unsupported, list):
        unsupported = []
    unsupported_count = len(unsupported)
    if unsupported_count == 0:
        return 0.0, 0

    sentences = [s for s in re.split(r"[.!?]+", answer or "") if s.strip()]
    denom = max(1, len(sentences))
    return min(1.0, unsupported_count / denom), unsupported_count


async def evaluate_case(answer_service, case: OfflineEvalCase, run_id: str) -> OfflineEvalCaseResult:
    """Run one case through AnswerService and compute split metrics."""
    trace_id = f"offline-eval-{run_id}-{uuid4().hex[:8]}"
    output = await answer_service.generate(
        query=case.input,
        conversation_history=case.conversation_history or None,
        trace_id=trace_id,
    )
    debug = output.debug if isinstance(output.debug, dict) else {}

    retrieval_recall, matched_chunk_ids = _retrieval_recall(case, debug)
    evidence_coverage, matched_requirements = _evidence_coverage(case, debug)
    answer_correctness, missing_expected, forbidden_violations = _answer_correctness(case, output.answer or "")
    citation_validity, citation_valid, citation_total = _citation_validity(output.citations or [], debug)
    hallucination_rate, unsupported_claim_count = _hallucination_rate(output.answer or "", debug)

    metrics: dict[str, Any] = {
        "retrieval_recall": retrieval_recall,
        "matched_chunk_ids": matched_chunk_ids,
        "evidence_coverage": evidence_coverage,
        "matched_requirements": matched_requirements,
        "answer_correctness": answer_correctness,
        "missing_expected": missing_expected,
        "forbidden_violations": forbidden_violations,
        "hallucination_rate": hallucination_rate,
        "unsupported_claim_count": unsupported_claim_count,
        "citation_validity": citation_validity,
        "citation_valid_count": citation_valid,
        "citation_total": citation_total,
        "decision_match": (
            (output.decision == case.expected_decision) if case.expected_decision else None
        ),
    }

    checks: list[bool] = []
    if case.expected_decision:
        checks.append(output.decision == case.expected_decision)
    if retrieval_recall is not None:
        checks.append(retrieval_recall >= 0.5)
    if evidence_coverage is not None:
        checks.append(evidence_coverage >= 0.5)
    if answer_correctness is not None:
        checks.append(answer_correctness >= case.correctness_threshold)
    if hallucination_rate is not None:
        checks.append(hallucination_rate <= case.hallucination_threshold)
    if citation_validity is not None:
        checks.append(citation_validity >= 0.8)

    passed = all(checks) if checks else True
    outcome = "pass" if passed else "fail"
    offline_eval_cases_total.labels(outcome=outcome).inc()
    if retrieval_recall is not None:
        offline_eval_retrieval_recall.observe(retrieval_recall)
    if evidence_coverage is not None:
        offline_eval_evidence_coverage.observe(evidence_coverage)
    if answer_correctness is not None:
        offline_eval_answer_correctness.observe(answer_correctness)
    if hallucination_rate is not None:
        offline_eval_hallucination_rate.observe(hallucination_rate)

    return OfflineEvalCaseResult(
        run_id=run_id,
        case_name=case.name,
        input=case.input,
        passed=passed,
        output_decision=output.decision,
        output_confidence=float(getattr(output, "confidence", 0.0) or 0.0),
        metrics=metrics,
        output_preview=(output.answer or "")[:400],
        tags=case.tags,
    )


def _avg(values: list[float | None]) -> float | None:
    cleaned = [float(v) for v in values if isinstance(v, (int, float))]
    if not cleaned:
        return None
    return mean(cleaned)


async def run_offline_eval(answer_service, cases: list[OfflineEvalCase], run_id: str | None = None) -> tuple[OfflineEvalRunSummary, list[OfflineEvalCaseResult]]:
    """Execute an offline evaluation run."""
    run_id = run_id or uuid4().hex[:12]
    results: list[OfflineEvalCaseResult] = []
    try:
        for case in cases:
            results.append(await evaluate_case(answer_service, case, run_id))
    except Exception:
        offline_eval_runs_total.labels(status="failed").inc()
        raise

    offline_eval_runs_total.labels(status="success").inc()
    pass_count = sum(1 for r in results if r.passed)
    fail_count = len(results) - pass_count
    summary = OfflineEvalRunSummary(
        run_id=run_id,
        case_count=len(results),
        pass_count=pass_count,
        fail_count=fail_count,
        pass_rate=(pass_count / len(results)) if results else 0.0,
        retrieval_recall_avg=_avg([r.metrics.get("retrieval_recall") for r in results]),
        evidence_coverage_avg=_avg([r.metrics.get("evidence_coverage") for r in results]),
        answer_correctness_avg=_avg([r.metrics.get("answer_correctness") for r in results]),
        hallucination_rate_avg=_avg([r.metrics.get("hallucination_rate") for r in results]),
        citation_validity_avg=_avg([r.metrics.get("citation_validity") for r in results]),
    )
    return summary, results


async def persist_eval_run(cases: list[OfflineEvalCase], results: list[OfflineEvalCaseResult]) -> None:
    """Persist cases/results into eval_cases + eval_results tables."""
    if not cases or not results:
        return
    from app.db.models import EvalCase, EvalResult
    from app.db.session import db_session

    by_name: dict[str, OfflineEvalCase] = {case.name: case for case in cases}

    async with db_session() as session:
        for result in results:
            case = by_name.get(result.case_name)
            if case is None:
                continue

            existing_q = await session.execute(
                select(EvalCase).where(
                    EvalCase.name == case.name,
                    EvalCase.input == case.input,
                )
            )
            db_case = existing_q.scalars().first()
            expected_tags = {
                "tags": case.tags,
                "required_evidence": case.required_evidence,
                "expected_decision": case.expected_decision,
                "metadata": case.metadata,
            }

            if db_case is None:
                db_case = EvalCase(
                    name=case.name,
                    input=case.input,
                    expected_policy_tags=expected_tags,
                )
                session.add(db_case)
                await session.flush()
            else:
                db_case.expected_policy_tags = expected_tags
                await session.flush()

            session.add(
                EvalResult(
                    eval_case_id=db_case.id,
                    run_id=result.run_id,
                    pass_=result.passed,
                    metrics=result.metrics,
                )
            )
        await session.flush()


def dump_eval_run_json(
    path: str | Path,
    summary: OfflineEvalRunSummary,
    results: list[OfflineEvalCaseResult],
) -> None:
    """Write eval summary + case results to JSON file."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "summary": asdict(summary),
        "results": [asdict(r) for r in results],
    }
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("offline_eval_written", path=str(p), cases=len(results))
