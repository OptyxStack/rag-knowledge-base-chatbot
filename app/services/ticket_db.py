"""Ticket DB operations - upsert from crawl."""

from sqlalchemy import select

from app.db.models import Ticket
from app.db.session import async_session_factory


async def upsert_ticket_from_crawl(t: dict) -> bool:
    """Upsert ticket from crawl dict. Returns True on success."""
    ext_id = str(t.get("external_id", "")).strip()
    if not ext_id:
        return False
    try:
        async with async_session_factory() as session:
            result = await session.execute(
                select(Ticket).where(Ticket.external_id == ext_id)
            )
            row = result.scalar_one_or_none()

            meta = dict(t.get("metadata") or {})
            if t.get("detail_url"):
                meta["detail_url"] = t["detail_url"]

            if row:
                row.subject = t.get("subject", "")
                row.description = t.get("description")
                row.status = t.get("status", "Open")
                row.priority = t.get("priority")
                row.client_id = t.get("client_id")
                row.email = t.get("email")
                row.name = t.get("name")
                row.ticket_metadata = meta or None
                row.source_file = t.get("source_file") or "sample_conversations.json"
            else:
                ticket = Ticket(
                    external_id=ext_id,
                    subject=(t.get("subject") or "")[:512],
                    description=t.get("description"),
                    status=(t.get("status") or "Open")[:64],
                    priority=t.get("priority"),
                    client_id=t.get("client_id"),
                    email=t.get("email"),
                    name=t.get("name"),
                    ticket_metadata=meta or None,
                    source_file=t.get("source_file") or "sample_conversations.json",
                )
                session.add(ticket)
            await session.commit()
            return True
    except Exception:
        return False
