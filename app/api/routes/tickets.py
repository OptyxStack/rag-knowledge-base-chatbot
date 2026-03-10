"""Tickets API routes - list and get from DB."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_, select

from app.core.auth import verify_api_key
from app.db.models import Ticket
from app.db.session import get_db
from sqlalchemy.ext.asyncio import AsyncSession

router = APIRouter(prefix="/tickets", tags=["tickets"])


@router.get("")
async def list_tickets(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = Query(None, description="Filter by status"),
    approval_status: str | None = Query(None, description="Filter by approval: pending, approved, rejected"),
    q: str | None = Query(None, description="Search in id, subject, description, and customer fields"),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """List tickets with pagination and filters."""
    offset = (page - 1) * page_size
    base = select(Ticket)
    count_base = select(func.count()).select_from(Ticket)
    if status and status.strip():
        base = base.where(Ticket.status.ilike(f"%{status.strip()}%"))
        count_base = count_base.where(Ticket.status.ilike(f"%{status.strip()}%"))
    if approval_status and approval_status.strip():
        base = base.where(Ticket.approval_status == approval_status.strip())
        count_base = count_base.where(Ticket.approval_status == approval_status.strip())
    if q and q.strip():
        search = f"%{q.strip()}%"
        search_filter = or_(
            Ticket.external_id.ilike(search),
            Ticket.subject.ilike(search),
            Ticket.description.ilike(search),
            Ticket.email.ilike(search),
            Ticket.name.ilike(search),
            Ticket.client_id.ilike(search),
            Ticket.source_file.ilike(search),
        )
        base = base.where(search_filter)
        count_base = count_base.where(search_filter)

    count_result = await db.execute(count_base)
    total = count_result.scalar() or 0

    base = base.order_by(Ticket.updated_at.desc()).offset(offset).limit(page_size)
    result = await db.execute(base)
    rows = result.scalars().all()

    items = [
        {
            "id": r.id,
            "external_id": r.external_id,
            "subject": r.subject,
            "description": (r.description or "")[:500],
            "status": r.status,
            "priority": r.priority,
            "client_id": r.client_id,
            "email": r.email,
            "name": r.name,
            "approval_status": r.approval_status,
            "metadata": r.ticket_metadata,
            "source_file": r.source_file,
            "detail_url": (r.ticket_metadata or {}).get("detail_url") if isinstance(r.ticket_metadata, dict) else None,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
        }
        for r in rows
    ]

    return {"items": items, "total": total, "page": page, "page_size": page_size}


@router.get("/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Get ticket by ID (UUID) or external_id."""
    # Try by UUID first
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id).limit(1))
    row = result.scalar_one_or_none()
    if not row:
        result = await db.execute(select(Ticket).where(Ticket.external_id == ticket_id).limit(1))
        row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")

    detail_url = None
    if row.ticket_metadata and isinstance(row.ticket_metadata, dict):
        detail_url = row.ticket_metadata.get("detail_url")

    return {
        "id": row.id,
        "external_id": row.external_id,
        "subject": row.subject,
        "description": row.description,
        "status": row.status,
        "priority": row.priority,
        "client_id": row.client_id,
        "email": row.email,
        "name": row.name,
        "approval_status": row.approval_status,
        "metadata": row.ticket_metadata,
        "source_file": row.source_file,
        "detail_url": detail_url,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
