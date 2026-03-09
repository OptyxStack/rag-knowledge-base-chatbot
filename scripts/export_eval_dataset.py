"""Export replay-seed eval cases from production conversations."""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from pathlib import Path

from sqlalchemy import Select, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_PHONE_RE = re.compile(r"\+?\d[\d\-\s()]{7,}\d")


def _redact(text: str, enabled: bool) -> str:
    if not enabled:
        return text
    out = _EMAIL_RE.sub("[redacted_email]", text or "")
    out = _PHONE_RE.sub("[redacted_phone]", out)
    return out


async def _run(args) -> int:
    from app.db.models import Conversation, Message
    from app.db.session import db_session

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    async with db_session() as session:
        stmt: Select = (
            select(Message, Conversation)
            .join(Conversation, Conversation.id == Message.conversation_id)
            .where(Message.role == "user")
            .order_by(Message.created_at.desc())
            .limit(args.limit)
        )
        if args.source_type:
            stmt = stmt.where(Conversation.source_type == args.source_type)

        rows = (await session.execute(stmt)).all()

    if not rows:
        print("No user messages found for export.")
        return 1

    written = 0
    with out_path.open("w", encoding="utf-8") as f:
        for idx, (msg, conv) in enumerate(rows, start=1):
            content = (msg.content or "").strip()
            if not content:
                continue
            content = _redact(content, enabled=args.redact)
            if len(content) < args.min_chars:
                continue

            case = {
                "name": f"prod_{conv.source_type}_{conv.source_id}_{idx}",
                "input": content,
                "tags": ["production_seed", str(conv.source_type)],
                "conversation_history": [],
                "expected_decision": None,
                "expected_chunk_ids": [],
                "required_evidence": [],
                "expected_answer_contains": [],
                "forbidden_answer_contains": [],
                "metadata": {
                    "source_type": conv.source_type,
                    "source_id": conv.source_id,
                    "message_id": msg.id,
                },
            }
            f.write(json.dumps(case, ensure_ascii=False) + "\n")
            written += 1

    print(f"Wrote {written} seed cases to {out_path}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Export offline eval seed dataset from user messages.")
    parser.add_argument("--output", required=True, help="Destination JSONL path.")
    parser.add_argument("--limit", type=int, default=200, help="Max number of user messages to export.")
    parser.add_argument("--source-type", default="", help="Optional filter: ticket or livechat.")
    parser.add_argument("--min-chars", type=int, default=12, help="Skip very short messages.")
    parser.set_defaults(redact=True)
    parser.add_argument("--no-redact", dest="redact", action="store_false", help="Disable email/phone redaction.")
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
