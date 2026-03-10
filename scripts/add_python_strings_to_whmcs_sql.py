#!/usr/bin/env python3
"""Extract text from a Python file and generate ticket records for SQL or app DB."""

from __future__ import annotations

import argparse
import ast
import random
import re
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


INSERT_PREFIX = (
    "INSERT INTO `tbltickets` "
    "(`id`, `tid`, `did`, `userid`, `contactid`, `requestor_id`, "
    "`prevent_client_closure`, `name`, `email`, `cc`, `c`, `ipaddress`, `date`, "
    "`title`, `message`, `status`, `urgency`, `admin`, `attachment`, "
    "`attachments_removed`, `lastreply`, `flag`, `clientunread`, `adminunread`, "
    "`replyingadmin`, `replyingtime`, `service`, `merged_ticket_id`, `editor`, "
    "`pinned_at`, `updated_at`) VALUES\n"
)


@dataclass(frozen=True)
class ExtractedText:
    kind: str
    location: str
    text: str


class PythonStringExtractor(ast.NodeVisitor):
    def __init__(self, source_path: Path) -> None:
        self.source_path = source_path
        self.items: list[ExtractedText] = []

    def visit_Module(self, node: ast.Module) -> None:
        self._collect_docstring(node, "module")
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._collect_docstring(node, f"function:{node.name}")
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._collect_docstring(node, f"async_function:{node.name}")
        self.generic_visit(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._collect_docstring(node, f"class:{node.name}")
        self.generic_visit(node)

    def visit_Constant(self, node: ast.Constant) -> None:
        if isinstance(node.value, str) and self._should_keep(node.value):
            self.items.append(
                ExtractedText(
                    kind="string",
                    location=f"L{getattr(node, 'lineno', 0)}",
                    text=node.value,
                )
            )

    def _collect_docstring(self, node: ast.AST, location: str) -> None:
        value = ast.get_docstring(node, clean=False)
        if value and self._should_keep(value):
            self.items.append(ExtractedText(kind="docstring", location=location, text=value))

    @staticmethod
    def _should_keep(value: str) -> bool:
        text = value.strip()
        return bool(text)


def extract_texts(py_file: Path, dedupe: bool) -> list[ExtractedText]:
    tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
    extractor = PythonStringExtractor(py_file)
    extractor.visit(tree)

    if not dedupe:
        return extractor.items

    seen: set[str] = set()
    items: list[ExtractedText] = []
    for item in extractor.items:
        key = item.text.strip()
        if key in seen:
            continue
        seen.add(key)
        items.append(item)
    return items


def sql_escape(value: str) -> str:
    return (
        value.replace("\\", "\\\\")
        .replace("'", "\\'")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace("\x00", "")
    )


def random_token(length: int = 8) -> str:
    alphabet = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    return "".join(random.choice(alphabet) for _ in range(length))


def next_ticket_numbers(sql_text: str, count: int) -> tuple[list[int], list[str]]:
    ids = [int(match.group(1)) for match in re.finditer(r"\((\d+),\s*'[^']*',", sql_text)]
    tids = [int(match.group(1)) for match in re.finditer(r"\(\d+,\s*'(\d+)',", sql_text)]

    next_id = (max(ids) if ids else 0) + 1
    next_tid = (max(tids) if tids else 100000) + 1

    return (
        list(range(next_id, next_id + count)),
        [f"{value:06d}" for value in range(next_tid, next_tid + count)],
    )


def build_rows(
    items: list[ExtractedText],
    start_ids: list[int],
    tids: list[str],
    source_label: str,
) -> str:
    now = datetime.now(UTC).strftime("%Y-%m-%d %H:%M:%S")
    rows: list[str] = []
    for idx, item in enumerate(items):
        title = f"{source_label} [{item.kind}] {item.location}"
        message = item.text.strip()
        row = (
            f"({start_ids[idx]}, '{tids[idx]}', 1, 0, 0, 0, 0, "
            f"'Auto Import', 'noreply@example.com', '', '{random_token()}', NULL, "
            f"'{now}', '{sql_escape(title)}', '{sql_escape(message)}', "
            f"'Closed', 'Medium', 'Codex', '', 0, '{now}', 0, 1, '1', 0, "
            f"'0000-00-00 00:00:00', '', 0, 'plain', NULL, '0000-00-00 00:00:00')"
        )
        rows.append(row)
    return ",\n".join(rows) + ";\n"


def validate_append_target(sql_text: str) -> None:
    if not sql_text.rstrip().endswith(";"):
        raise RuntimeError(
            "Target SQL file is incomplete or does not end with ';'. "
            "Refusing to append into an invalid dump."
        )


async def ensure_migrations() -> None:
    from sqlalchemy import text

    from app.db.session import engine

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1 FROM tickets LIMIT 1"))
    except Exception:
        import subprocess

        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parent.parent,
            check=False,
        )
        if result.returncode != 0:
            raise RuntimeError("Migrations failed. Run: alembic upgrade head")


async def insert_into_app_db(
    items: list[ExtractedText],
    source_path: Path,
    ids: list[int],
    skip_existing: bool,
) -> tuple[int, int]:
    from sqlalchemy import select

    from app.db.models import Ticket
    from app.db.session import async_session_factory

    await ensure_migrations()

    created = 0
    skipped = 0
    async with async_session_factory() as session:
        for idx, item in enumerate(items):
            external_id = f"pystr-{source_path.stem}-{ids[idx]}"
            existing = await session.execute(
                select(Ticket).where(Ticket.external_id == external_id)
            )
            if existing.scalar_one_or_none():
                if skip_existing:
                    skipped += 1
                    continue

            ticket = Ticket(
                external_id=external_id,
                subject=f"{source_path.name} [{item.kind}] {item.location}",
                description=item.text.strip(),
                status="Closed",
                priority="Medium",
                client_id=None,
                email="noreply@example.com",
                name="Auto Import",
                ticket_metadata={
                    "source": "python_string_import",
                    "source_path": str(source_path).replace("\\", "/"),
                    "kind": item.kind,
                    "location": item.location,
                },
                source_file=source_path.name,
                approval_status="approved",
            )
            session.add(ticket)
            created += 1
        await session.commit()

    return created, skipped


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract strings/docstrings from a Python file and append them as tbltickets rows."
    )
    parser.add_argument("--python-file", required=True, help="Python source file to read")
    parser.add_argument("--sql-file", help="WHMCS SQL dump to append to")
    parser.add_argument(
        "--output-sql",
        help="Write generated INSERT block to a separate file instead of appending to --sql-file",
    )
    parser.add_argument(
        "--no-dedupe",
        action="store_true",
        help="Keep duplicate strings instead of deduplicating identical text",
    )
    parser.add_argument(
        "--append",
        action="store_true",
        help="Append directly to --sql-file. Without this flag, the script only prints a summary unless --output-sql is set.",
    )
    parser.add_argument(
        "--insert-db",
        action="store_true",
        help="Insert extracted data directly into the app PostgreSQL tickets table.",
    )
    parser.add_argument(
        "--no-skip-existing",
        action="store_true",
        help="When used with --insert-db, do not skip existing external_id values.",
    )
    args = parser.parse_args()

    python_file = Path(args.python_file)
    sql_file = Path(args.sql_file) if args.sql_file else None
    output_sql = Path(args.output_sql) if args.output_sql else None

    if not python_file.exists():
        raise FileNotFoundError(f"Python file not found: {python_file}")
    if (args.append or output_sql) and not sql_file:
        raise ValueError("--sql-file is required when using --append or --output-sql")
    if sql_file and not sql_file.exists():
        raise FileNotFoundError(f"SQL file not found: {sql_file}")

    items = extract_texts(python_file, dedupe=not args.no_dedupe)
    if not items:
        print("No text extracted.")
        return

    sql_text = sql_file.read_text(encoding="utf-8", errors="ignore") if sql_file else ""
    ids, tids = next_ticket_numbers(sql_text, len(items))

    print(f"Extracted {len(items)} text entries from {python_file}")
    print(f"New ticket ids: {ids[0]}..{ids[-1]}")
    print(f"New tids: {tids[0]}..{tids[-1]}")

    if output_sql:
        insert_sql = INSERT_PREFIX + build_rows(items, ids, tids, python_file.name)
        output_sql.write_text(insert_sql, encoding="utf-8")
        print(f"Wrote generated SQL to {output_sql}")

    if args.append:
        insert_sql = INSERT_PREFIX + build_rows(items, ids, tids, python_file.name)
        validate_append_target(sql_text)
        with sql_file.open("a", encoding="utf-8", newline="") as handle:
            if not sql_text.endswith("\n"):
                handle.write("\n")
            handle.write("\n")
            handle.write(insert_sql)
        print(f"Appended INSERT block to {sql_file}")

    if args.insert_db:
        import asyncio

        created, skipped = asyncio.run(
            insert_into_app_db(
                items,
                python_file,
                ids,
                skip_existing=not args.no_skip_existing,
            )
        )
        print(f"Inserted into DB: {created} created, {skipped} skipped")


if __name__ == "__main__":
    main()
