#!/usr/bin/env python3
"""Check and delete NOTI (notification) tickets from the tickets table."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select

from app.crawlers.whmcs import _is_noti_ticket
from app.db.models import Ticket
from app.db.session import async_session_factory


async def find_and_delete_noti_tickets(dry_run: bool = True) -> tuple[int, int]:
    """Find NOTI tickets and optionally delete them. Returns (found_count, deleted_count)."""
    found: list[Ticket] = []
    async with async_session_factory() as session:
        result = await session.execute(select(Ticket))
        all_tickets = result.scalars().all()

        for t in all_tickets:
            ticket_dict = {
                "subject": t.subject,
                "email": t.email,
                "name": t.name,
            }
            if _is_noti_ticket(ticket_dict):
                found.append(t)

        if not found:
            return 0, 0

        print(f"Found {len(found)} NOTI ticket(s):")
        for t in found:
            print(f"  - id={t.id}, external_id={t.external_id}, subject={t.subject!r}")

        if dry_run:
            print("\n[DRY RUN] No records deleted. Run with --delete to actually delete.")
            return len(found), 0

        for t in found:
            await session.delete(t)
        await session.commit()
        print(f"\nDeleted {len(found)} NOTI ticket(s).")
        return len(found), len(found)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Find and delete NOTI tickets from DB")
    parser.add_argument("--delete", action="store_true", help="Actually delete (default: dry run)")
    args = parser.parse_args()

    found, deleted = asyncio.run(find_and_delete_noti_tickets(dry_run=not args.delete))
    if found == 0:
        print("No NOTI tickets found in the tickets table.")


if __name__ == "__main__":
    main()
