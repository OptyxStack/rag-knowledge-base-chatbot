# Offline Eval (Phase 4)

This project now supports replay-style offline evaluation with split metrics:

- retrieval recall
- evidence coverage
- answer correctness
- hallucination rate
- citation validity

## 1) Build a seed dataset from production queries

Export recent user messages from `conversations/messages` into JSONL:

```bash
python scripts/export_eval_dataset.py --output artifacts/eval_seed.jsonl --limit 500
```

Optional filters:

- `--source-type ticket`
- `--source-type livechat`
- `--no-redact` to disable default email/phone redaction

The exported JSONL is a seed for manual labeling (`expected_*` fields).

## 2) Run offline eval

```bash
python scripts/run_offline_eval.py --dataset tests/fixtures/offline_eval_replay_cases.jsonl --output artifacts/offline_eval.json
```

Persist run to DB (`eval_cases`, `eval_results`):

```bash
python scripts/run_offline_eval.py --dataset artifacts/eval_labeled.jsonl --persist
```

## 3) Dataset format (JSONL)

Each line is a case:

```json
{
  "name": "policy_question_case",
  "input": "what is your refund policy?",
  "tags": ["policy_question", "replay"],
  "conversation_history": [],
  "expected_decision": "PASS",
  "expected_chunk_ids": ["chunk-policy-1"],
  "required_evidence": ["policy_language"],
  "expected_answer_contains": ["refund"],
  "forbidden_answer_contains": ["guaranteed forever"],
  "correctness_threshold": 0.7,
  "hallucination_threshold": 0.2,
  "metadata": {}
}
```

## 4) Replay regression classes

`tests/fixtures/offline_eval_replay_cases.jsonl` includes baseline classes:

- ambiguous referent
- policy questions
- pricing questions
- troubleshooting steps
- multilingual queries
