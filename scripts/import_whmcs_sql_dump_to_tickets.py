#!/usr/bin/env python3
"""Import WHMCS tbltickets + tblticketreplies from MySQL dumps into the app PostgreSQL tickets table."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


TICKET_COLUMN_COUNT = 31
REPLY_COLUMN_COUNT = 14


def mysql_unescape(value: str) -> str:
    mapping = {
        "0": "\0",
        "b": "\b",
        "n": "\n",
        "r": "\r",
        "t": "\t",
        "Z": "\x1a",
        "\\": "\\",
        "'": "'",
        '"': '"',
        "%": "%",
        "_": "_",
    }
    chars: list[str] = []
    i = 0
    while i < len(value):
        ch = value[i]
        if ch != "\\":
            chars.append(ch)
            i += 1
            continue

        i += 1
        if i >= len(value):
            chars.append("\\")
            break

        chars.append(mapping.get(value[i], value[i]))
        i += 1

    return "".join(chars)


def parse_field(token: str):
    if token == "NULL":
        return None
    if token.startswith("'") and token.endswith("'"):
        return mysql_unescape(token[1:-1])
    if token == "":
        return ""
    try:
        return int(token)
    except ValueError:
        return token


def parse_tuple_line(line: str, expected_columns: int = TICKET_COLUMN_COUNT) -> list:
    text = line.strip()
    if text.endswith(","):
        text = text[:-1]
    if text.endswith(";"):
        text = text[:-1]
    if not text.startswith("(") or not text.endswith(")"):
        raise ValueError("line is not a complete SQL tuple")

    inner = text[1:-1]
    tokens: list[str] = []
    buf: list[str] = []
    in_string = False
    escaped = False

    for ch in inner:
        if in_string:
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == "'":
                in_string = False
            continue

        if ch == "'":
            in_string = True
            buf.append(ch)
        elif ch == ",":
            tokens.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)

    tokens.append("".join(buf).strip())
    if len(tokens) != expected_columns:
        raise ValueError(f"expected {expected_columns} columns, got {len(tokens)}")

    return [parse_field(token) for token in tokens]


def load_replies_from_sql(replies_file: Path) -> dict[int, list[dict]]:
    """Load tblticketreplies from SQL dump. Returns tid -> list of {role, content, name, posted}."""
    tid_to_replies: dict[int, list[dict]] = {}
    started = False

    with replies_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not started:
                if "INSERT INTO `tblticketreplies`" in line:
                    started = True
                continue

            if not line.startswith("("):
                continue

            try:
                row = parse_tuple_line(line, expected_columns=REPLY_COLUMN_COUNT)
            except Exception:
                continue

            # id, tid, userid, contactid, requestor_id, name, email, date, message, admin, attachment, attachments_removed, rating, editor
            (
                _reply_id,
                tid,
                _userid,
                _contactid,
                _requestor_id,
                name,
                _email,
                date_val,
                message,
                admin,
                _attachment,
                _attachments_removed,
                _rating,
                _editor,
            ) = row

            tid_int = int(tid) if tid is not None else 0
            content = (message or "").strip()
            if not content:
                continue

            # Staff: admin is non-empty. Customer: admin is empty.
            role = "staff" if (admin and str(admin).strip()) else "customer"
            reply_obj = {
                "role": role,
                "content": content,
                "name": (name or "").strip() or None,
                "posted": str(date_val) if date_val else None,
            }

            if tid_int not in tid_to_replies:
                tid_to_replies[tid_int] = []
            tid_to_replies[tid_int].append(reply_obj)

    return tid_to_replies


def normalize_text(value: str | None, max_len: int | None = None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if cleaned == "":
        return None
    if max_len is not None and len(cleaned) > max_len:
        return cleaned[:max_len]
    return cleaned


def build_ticket_payload(
    row: list,
    source_file: str,
    approval_status: str,
    replies_by_tid: dict[int, list[dict]] | None = None,
) -> dict:
    (
        whmcs_id,
        tid,
        did,
        userid,
        contactid,
        requestor_id,
        prevent_client_closure,
        name,
        email,
        cc,
        c_value,
        ipaddress,
        date_value,
        title,
        message,
        status,
        urgency,
        admin,
        attachment,
        attachments_removed,
        lastreply,
        flag,
        clientunread,
        adminunread,
        replyingadmin,
        replyingtime,
        service,
        merged_ticket_id,
        editor,
        pinned_at,
        updated_at,
    ) = row

    metadata: dict = {
        "source": "whmcs_sql_dump",
        "whmcs_id": whmcs_id,
        "tid": tid,
        "department_id": did,
        "contact_id": contactid,
        "requestor_id": requestor_id,
        "prevent_client_closure": prevent_client_closure,
        "cc": cc,
        "c": c_value,
        "ipaddress": ipaddress,
        "date": date_value,
        "admin": admin,
        "attachment": attachment,
        "attachments_removed": attachments_removed,
        "lastreply": lastreply,
        "flag": flag,
        "clientunread": clientunread,
        "adminunread": adminunread,
        "replyingadmin": replyingadmin,
        "replyingtime": replyingtime,
        "service": service,
        "merged_ticket_id": merged_ticket_id,
        "editor": editor,
        "pinned_at": pinned_at,
        "updated_at": updated_at,
    }

    # Join replies: tblticketreplies.tid = tbltickets.id (whmcs_id)
    if replies_by_tid:
        ticket_id = int(whmcs_id) if whmcs_id is not None else 0
        replies = replies_by_tid.get(ticket_id, [])
        if replies:
            metadata["replies"] = replies

    return {
        "id": str(uuid4()),
        "external_id": str(whmcs_id),
        "subject": normalize_text(title, 512) or f"WHMCS Ticket {whmcs_id}",
        "description": normalize_text(message) or "",
        "status": normalize_text(status, 64) or "Open",
        "priority": normalize_text(urgency, 32),
        "client_id": None if not userid else str(userid),
        "email": normalize_text(email, 256),
        "name": normalize_text(name, 256),
        "ticket_metadata": metadata,
        "source_file": source_file,
        "approval_status": approval_status,
    }


async def truncate_tickets() -> None:
    """Delete all tickets (for --replace before re-import)."""
    from sqlalchemy import text

    from app.db.models import Ticket
    from app.db.session import async_session_factory

    async with async_session_factory() as session:
        await session.execute(text("TRUNCATE TABLE tickets CASCADE"))
        await session.commit()
    print("Truncated tickets table.")


async def insert_batch(batch: list[dict], dry_run: bool = False) -> int:
    if not batch:
        return 0
    if dry_run:
        return len(batch)

    from sqlalchemy.dialects.postgresql import insert

    from app.db.models import Ticket
    from app.db.session import async_session_factory

    stmt = insert(Ticket).values(batch)
    stmt = stmt.on_conflict_do_nothing(index_elements=["external_id"])

    async with async_session_factory() as session:
        result = await session.execute(stmt)
        await session.commit()
        return result.rowcount or 0


async def import_dump(
    sql_file: Path,
    batch_size: int,
    approval_status: str,
    replies_file: Path | None = None,
    replace: bool = False,
    dry_run: bool = False,
) -> tuple[int, int, int]:
    inserted = 0
    seen = 0
    skipped_malformed = 0
    batch: list[dict] = []
    started = False

    if replace and not dry_run:
        await truncate_tickets()

    replies_by_tid: dict[int, list[dict]] = {}
    if replies_file and replies_file.exists():
        print(f"Loading replies from {replies_file}...")
        replies_by_tid = load_replies_from_sql(replies_file)
        print(f"Loaded {sum(len(r) for r in replies_by_tid.values())} replies for {len(replies_by_tid)} tickets")

    with sql_file.open("r", encoding="utf-8", errors="ignore") as handle:
        for line in handle:
            if not started:
                if line.startswith("INSERT INTO `tbltickets`"):
                    started = True
                continue

            if not line.startswith("("):
                continue

            try:
                row = parse_tuple_line(line, expected_columns=TICKET_COLUMN_COUNT)
            except Exception:
                skipped_malformed += 1
                continue

            batch.append(
                build_ticket_payload(
                    row, sql_file.name, approval_status, replies_by_tid=replies_by_tid or None
                )
            )
            seen += 1

            if len(batch) >= batch_size:
                inserted += await insert_batch(batch, dry_run=dry_run)
                print(f"Progress: parsed {seen}, inserted {inserted}")
                batch = []

    if batch:
        inserted += await insert_batch(batch, dry_run=dry_run)

    return seen, inserted, skipped_malformed


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Import WHMCS tbltickets + tblticketreplies into app PostgreSQL tickets."
    )
    parser.add_argument(
        "--sql-file",
        default="source/greenvps_whmcs.sql",
        help="Path to tbltickets SQL dump",
    )
    parser.add_argument(
        "--replies-file",
        default="source/greenvps_whmcs-v2.sql",
        help="Path to tblticketreplies SQL dump (optional, for staff replies)",
    )
    parser.add_argument("--batch-size", type=int, default=1000, help="Bulk insert batch size")
    parser.add_argument(
        "--approval-status",
        default="approved",
        choices=["pending", "approved", "rejected"],
        help="Approval status to assign to imported tickets",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="Truncate tickets table before import (clean re-import with replies)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate only, do not insert into DB",
    )
    args = parser.parse_args()

    sql_file = Path(args.sql_file)
    if not sql_file.exists():
        raise FileNotFoundError(f"SQL dump not found: {sql_file}")

    replies_file = Path(args.replies_file) if args.replies_file else None

    seen, inserted, skipped_malformed = asyncio.run(
        import_dump(
            sql_file,
            batch_size=args.batch_size,
            approval_status=args.approval_status,
            replies_file=replies_file,
            replace=args.replace,
            dry_run=args.dry_run,
        )
    )
    print(
        f"Done: parsed {seen} rows, inserted {inserted} rows, skipped malformed {skipped_malformed}"
    )


if __name__ == "__main__":
    main()
