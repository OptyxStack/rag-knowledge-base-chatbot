"""Suggest Reply API - platform-agnostic endpoint for generating responses.

Use from any platform: ticket systems (WHMCS, Zendesk, etc.), livechat, helpdesk.
No conversation creation required. Stateless, one-shot.
"""

from fastapi import APIRouter, Depends, HTTPException

from app.api.schemas import SuggestReplyRequest, SuggestReplyResponse, CitationSchema
from app.core.auth import verify_api_key
from app.core.guardrails import check_injection, sanitize_user_input
from app.core.logging import get_logger
from app.core.tracing import get_trace_id
from app.services.answer_service import AnswerService

logger = get_logger(__name__)

router = APIRouter(prefix="/reply", tags=["reply"])


@router.post("/generate", response_model=SuggestReplyResponse)
async def generate_suggested_reply(
    body: SuggestReplyRequest,
    _auth: str = Depends(verify_api_key),
):
    """Generate a suggested reply for any platform (ticket, livechat, helpdesk).

    Stateless: no conversation or message is persisted.
    Use this when you need a one-shot response to display in your external system.

    Example: Ticket system sends subject + description → get suggested reply to show agent.
    """
    is_safe, attack = check_injection(body.query)
    if not is_safe:
        raise HTTPException(status_code=400, detail="Invalid request")
    query = sanitize_user_input(body.query)

    history = body.conversation_history or []
    trace_id = get_trace_id()

    answer_svc = AnswerService()
    output = await answer_svc.generate(
        query=query,
        conversation_history=history[-10:] if history else None,
        trace_id=trace_id,
    )

    citations = [
        CitationSchema(
            chunk_id=c.get("chunk_id", ""),
            source_url=c.get("source_url", ""),
            doc_type=c.get("doc_type", ""),
        )
        for c in output.citations
        if isinstance(c, dict) and c.get("chunk_id")
    ]

    return SuggestReplyResponse(
        answer=output.answer,
        decision=output.decision,
        followup_questions=output.followup_questions or [],
        citations=citations,
        confidence=output.confidence,
        debug=output.debug,
    )
