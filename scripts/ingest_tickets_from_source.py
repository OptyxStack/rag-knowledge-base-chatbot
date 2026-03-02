#!/usr/bin/env python3
"""
Ingest tickets from source/ JSON files into the database.
Run from project root: python scripts/ingest_tickets_from_source.py

Requires: PostgreSQL running (or docker-compose up).
"""
import argparse
import asyncio
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


async def ensure_migrations() -> None:
    """Run migrations if tickets table does not exist."""
    from sqlalchemy import text

    from app.db.session import engine

    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1 FROM tickets LIMIT 1"))
    except Exception:
        print("Tickets table not found. Running migrations...")
        import subprocess

        result = subprocess.run(
            ["alembic", "upgrade", "head"],
            cwd=Path(__file__).resolve().parent.parent,
        )
        if result.returncode != 0:
            raise RuntimeError("Migrations failed. Run: alembic upgrade head")
        print("Migrations complete.")


async def run_ingest(tickets: list[dict], skip_existing: bool = True) -> dict:
    """Upsert tickets into DB."""
    await ensure_migrations()

    from sqlalchemy import select

    from app.db.models import Ticket
    from app.db.session import async_session_factory

    results = {"created": 0, "updated": 0, "skipped": 0, "error": 0}

    for i, t in enumerate(tickets):
        try:
            async with async_session_factory() as session:
                existing = await session.execute(
                    select(Ticket).where(Ticket.external_id == t["external_id"])
                )
                row = existing.scalar_one_or_none()

                if row:
                    if skip_existing:
                        results["skipped"] += 1
                    else:
                        row.subject = t["subject"]
                        row.description = t.get("description")
                        row.status = t["status"]
                        row.priority = t.get("priority")
                        row.client_id = t.get("client_id")
                        row.email = t.get("email")
                        row.name = t.get("name")
                        row.ticket_metadata = t.get("metadata")
                        row.source_file = t.get("source_file")
                        await session.commit()
                        results["updated"] += 1
                else:
                    ticket = Ticket(
                        external_id=t["external_id"],
                        subject=t["subject"],
                        description=t.get("description"),
                        status=t["status"],
                        priority=t.get("priority"),
                        client_id=t.get("client_id"),
                        email=t.get("email"),
                        name=t.get("name"),
                        ticket_metadata=t.get("metadata"),
                        source_file=t.get("source_file"),
                        approval_status="approved",  # Tickets from file are pre-approved
                    )
                    session.add(ticket)
                    await session.commit()
                    results["created"] += 1
        except Exception as e:
            results["error"] += 1
            print(f"  Error ticket {i} ({t.get('external_id', '')}): {e}")

        if (i + 1) % 50 == 0:
            print(f"  Progress: {i + 1}/{len(tickets)}")

    return results


def main():
    parser = argparse.ArgumentParser(description="Ingest tickets from source/ JSON files")
    parser.add_argument("--source-dir", default="source", help="Path to source directory")
    parser.add_argument("--files", nargs="*", help="Specific files (default: sample_conversations.json)")
    parser.add_argument("--dry-run", action="store_true", help="Only load, don't ingest")
    parser.add_argument("--update-existing", action="store_true", help="Update existing tickets")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    if not source_dir.exists():
        print(f"Source directory not found: {source_dir}")
        sys.exit(1)

    from app.services.ticket_loaders import load_all_tickets

    files = args.files or ["sample_conversations.json", "tickets.json"]  # tickets.json for backward compat
    print(f"Loading tickets from {files}...")
    tickets = load_all_tickets(source_dir, files)
    print(f"Total tickets to ingest: {len(tickets)}")

    if not tickets:
        print("No tickets to ingest.")
        sys.exit(0)

    if args.dry_run:
        print("Dry run - skipping ingest.")
        print(f"Sample: {tickets[0]}")
        sys.exit(0)

    print("Running ingestion...")
    results = asyncio.run(run_ingest(tickets, skip_existing=not args.update_existing))
    print(
        f"Done: {results['created']} created, {results['updated']} updated, "
        f"{results['skipped']} skipped, {results['error']} errors"
    )


if __name__ == "__main__":
    main()
