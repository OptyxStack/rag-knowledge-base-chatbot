"""Run offline RAG evaluation against a JSONL replay dataset."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _run(args) -> int:
    from app.services.answer_service import AnswerService
    from app.services.offline_eval import (
        dump_eval_run_json,
        load_eval_cases_jsonl,
        persist_eval_run,
        run_offline_eval,
    )

    cases = load_eval_cases_jsonl(args.dataset)
    if not cases:
        print("No eval cases found.")
        return 1

    service = AnswerService()
    summary, results = await run_offline_eval(service, cases, run_id=args.run_id)

    output_path = Path(args.output) if args.output else Path("artifacts") / f"offline_eval_{summary.run_id}.json"
    dump_eval_run_json(output_path, summary, results)

    if args.persist:
        await persist_eval_run(cases, results)

    print(f"Run ID: {summary.run_id}")
    print(f"Cases: {summary.case_count} | Pass: {summary.pass_count} | Fail: {summary.fail_count} | Pass rate: {summary.pass_rate:.2%}")
    print(f"Retrieval recall avg: {summary.retrieval_recall_avg}")
    print(f"Evidence coverage avg: {summary.evidence_coverage_avg}")
    print(f"Answer correctness avg: {summary.answer_correctness_avg}")
    print(f"Hallucination rate avg: {summary.hallucination_rate_avg}")
    print(f"Citation validity avg: {summary.citation_validity_avg}")
    print(f"Result JSON: {output_path}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run offline eval replay for RAG pipeline.")
    parser.add_argument("--dataset", required=True, help="Path to eval dataset JSONL.")
    parser.add_argument("--output", default="", help="Optional output JSON path for summary/results.")
    parser.add_argument("--run-id", default="", help="Optional custom run ID.")
    parser.add_argument("--persist", action="store_true", help="Persist run into eval_cases/eval_results tables.")
    return parser


def main() -> int:
    parser = _build_parser()
    args = parser.parse_args()
    try:
        return asyncio.run(_run(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
