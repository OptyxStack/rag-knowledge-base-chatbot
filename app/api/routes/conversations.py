"""Conversation and message API routes."""

from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Header, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api.schemas import (
    AssistantMessageResponse,
    CitationSchema,
    ConversationDetailResponse,
    ConversationListResponse,
    ConversationResponse,
    CreateConversationRequest,
    MessageResponse,
    MessageCreate,
    SendMessageResponse,
    UpdateConversationRequest,
)
from app.core.auth import verify_api_key
from app.core.guardrails import check_injection, sanitize_user_input
from app.core.logging import get_logger
from app.core.tracing import get_trace_id
from app.db.models import Chunk, Conversation, Message, Citation
from app.db.session import get_db
from app.services.answer_service import AnswerService
from app.services.conversation_context import truncate_for_pipeline

logger = get_logger(__name__)

router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.get("", response_model=ConversationListResponse)
async def list_conversations(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    source_type: str | None = Query(None, description="Filter by ticket or livechat"),
    source_id: str | None = Query(None, description="Filter by ticket/livechat ID"),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """List conversations with pagination. Filter by source_type and source_id."""
    offset = (page - 1) * page_size
    q = select(Conversation)
    count_q = select(func.count()).select_from(Conversation)
    if source_type:
        q = q.where(Conversation.source_type == source_type)
        count_q = count_q.where(Conversation.source_type == source_type)
    if source_id:
        q = q.where(Conversation.source_id == source_id)
        count_q = count_q.where(Conversation.source_id == source_id)
    count_result = await db.execute(count_q)
    total = count_result.scalar() or 0
    result = await db.execute(
        q.order_by(Conversation.created_at.desc()).offset(offset).limit(page_size)
    )
    convs = result.scalars().all()
    items = [
        ConversationResponse(
            id=c.id,
            source_type=c.source_type,
            source_id=c.source_id,
            metadata=c.conv_metadata,
            created_at=c.created_at,
        )
        for c in convs
    ]
    return ConversationListResponse(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=ConversationResponse)
async def create_conversation(
    body: CreateConversationRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Create a new conversation. Must be linked to a ticket or livechat."""
    conv = Conversation(
        source_type=body.source_type,
        source_id=body.source_id,
        conv_metadata=body.metadata,
    )
    db.add(conv)
    await db.flush()
    await db.refresh(conv)
    return ConversationResponse(
        id=conv.id,
        source_type=conv.source_type,
        source_id=conv.source_id,
        metadata=conv.conv_metadata,
        created_at=conv.created_at,
    )


@router.get("/{conversation_id}", response_model=ConversationDetailResponse)
async def get_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Get conversation with messages."""
    result = await db.execute(
        select(Conversation)
        .where(Conversation.id == conversation_id)
        .options(
            selectinload(Conversation.messages).selectinload(Message.citations).selectinload(Citation.chunk).selectinload(Chunk.document)
        )
    )
    conv = result.scalars().first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    messages = []
    for m in conv.messages:
        citations = []
        for c in m.citations or []:
            source_url = ""
            doc_type = ""
            if c.chunk:
                doc_type = (c.chunk.chunk_metadata or {}).get("doc_type", "")
                if c.chunk.document:
                    source_url = c.chunk.document.source_url
            citations.append(CitationSchema(chunk_id=c.chunk_id, source_url=source_url, doc_type=doc_type))
        # Fetch source_url from chunk if needed - simplified here
        messages.append(
            MessageResponse(
                id=m.id,
                role=m.role,
                content=m.content,
                created_at=m.created_at,
                citations=citations if citations else None,
                debug=m.debug_metadata if m.role == "assistant" else None,
            )
        )

    return ConversationDetailResponse(
        id=conv.id,
        source_type=conv.source_type,
        source_id=conv.source_id,
        metadata=conv.conv_metadata,
        created_at=conv.created_at,
        messages=messages,
    )


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    body: UpdateConversationRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Update conversation metadata."""
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = result.scalars().first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    if body.metadata is not None:
        conv.conv_metadata = body.metadata
    await db.commit()
    await db.refresh(conv)
    return ConversationResponse(
        id=conv.id,
        source_type=conv.source_type,
        source_id=conv.source_id,
        metadata=conv.conv_metadata,
        created_at=conv.created_at,
    )


@router.delete("/{conversation_id}", status_code=204)
async def delete_conversation(
    conversation_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Delete conversation and its messages."""
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = result.scalars().first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    await db.delete(conv)
    await db.commit()


@router.post("/{conversation_id}/messages", response_model=SendMessageResponse)
async def send_message(
    conversation_id: str,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
    x_external_user_id: str | None = Header(None, alias="X-External-User-Id"),
):
    """Send a message and get assistant response (sync)."""
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = result.scalars().first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    # Guardrails: injection check
    is_safe, attack = check_injection(body.content)
    if not is_safe:
        raise HTTPException(status_code=400, detail="Invalid request")
    content = sanitize_user_input(body.content)

    # Save user message
    user_msg = Message(conversation_id=conversation_id, role="user", content=content)
    db.add(user_msg)
    await db.flush()

    # Get history
    hist_result = await db.execute(
        select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
    )
    history_msgs = hist_result.scalars().all()
    raw_history = [
        {"role": m.role, "content": m.content}
        for m in history_msgs
        if m.id != user_msg.id
    ]
    conversation_history = truncate_for_pipeline(raw_history)

    # Generate answer (tickets are retrieved via vector/RAG like docs when relevant)
    answer_svc = AnswerService()
    trace_id = get_trace_id()
    try:
        from app.core.logging import get_logger
        from app.services.flow_debug import _pipeline_log
        _pipeline_log("api", "message_received", conversation_id=conversation_id, trace_id=trace_id)
    except Exception:
        pass
    output = await answer_svc.generate(
        query=content,
        conversation_history=conversation_history,
        trace_id=trace_id,
    )

    # Save assistant message (with full flow debug: decision, confidence, followup, etc.)
    debug_meta = dict(output.debug or {})
    debug_meta["decision"] = output.decision
    debug_meta["confidence"] = output.confidence
    debug_meta["followup_questions"] = output.followup_questions
    assistant_msg = Message(
        conversation_id=conversation_id,
        role="assistant",
        content=output.answer,
        debug_metadata=debug_meta,
    )
    db.add(assistant_msg)
    await db.flush()

    try:
        from app.services.flow_debug import _pipeline_log
        _pipeline_log("api", "response_ready", decision=output.decision, confidence=output.confidence, trace_id=trace_id)
    except Exception:
        pass

    # Save citations
    for c in output.citations:
        cit = Citation(
            message_id=assistant_msg.id,
            chunk_id=c.get("chunk_id", ""),
            score=1.0,
        )
        db.add(cit)
    await db.commit()

    return SendMessageResponse(
        conversation_id=conversation_id,
        message=AssistantMessageResponse(
            message_id=assistant_msg.id,
            content=output.answer,
            decision=output.decision,
            followup_questions=output.followup_questions,
            citations=[
                CitationSchema(
                    chunk_id=c.get("chunk_id", ""),
                    source_url=c.get("source_url", ""),
                    doc_type=c.get("doc_type", ""),
                )
                for c in output.citations
                if isinstance(c, dict) and c.get("chunk_id")
            ],
            confidence=output.confidence,
            debug=output.debug,
            created_at=assistant_msg.created_at,
        ),
    )


@router.post("/{conversation_id}/messages:stream")
async def send_message_stream(
    conversation_id: str,
    body: MessageCreate,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Send a message and get SSE streaming response."""
    from fastapi.responses import StreamingResponse
    import json

    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conv = result.scalars().first()
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")

    is_safe, _ = check_injection(body.content)
    if not is_safe:
        raise HTTPException(status_code=400, detail="Invalid request")
    content = sanitize_user_input(body.content)

    async def event_generator():
        user_msg = Message(conversation_id=conversation_id, role="user", content=content)
        db.add(user_msg)
        await db.flush()

        hist_result = await db.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at)
        )
        history_msgs = hist_result.scalars().all()
        raw_history = [
            {"role": m.role, "content": m.content}
            for m in history_msgs
            if m.id != user_msg.id
        ]
        conversation_history = truncate_for_pipeline(raw_history)

        answer_svc = AnswerService()
        output = await answer_svc.generate(
            query=content,
            conversation_history=conversation_history,
            trace_id=get_trace_id(),
        )

        # Stream content chunks
        for i in range(0, len(output.answer), 100):
            chunk = output.answer[i : i + 100]
            yield f"data: {json.dumps({'type': 'content', 'data': chunk})}\n\n"

        yield f"data: {json.dumps({'type': 'citations', 'data': output.citations})}\n\n"
        yield f"data: {json.dumps({'type': 'done', 'data': {'decision': output.decision, 'confidence': output.confidence}})}\n\n"

        # Persist assistant message (with full flow debug)
        debug_meta = dict(output.debug or {})
        debug_meta["decision"] = output.decision
        debug_meta["confidence"] = output.confidence
        debug_meta["followup_questions"] = output.followup_questions
        assistant_msg = Message(
            conversation_id=conversation_id,
            role="assistant",
            content=output.answer,
            debug_metadata=debug_meta,
        )
        db.add(assistant_msg)
        await db.flush()
        for c in output.citations:
            cit = Citation(message_id=assistant_msg.id, chunk_id=c.get("chunk_id", ""), score=1.0)
            db.add(cit)
        await db.commit()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
