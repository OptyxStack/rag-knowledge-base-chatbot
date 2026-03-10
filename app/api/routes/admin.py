"""Admin API routes (ingest, config, intents, crawl)."""

import asyncio
import json
import queue
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    AppConfigResponse,
    AppConfigUpdateRequest,
    ArchiConfigResponse,
    ArchiConfigUpdateRequest,
    AutoGenerateBrandingRequest,
    AutoGenerateBrandingResponse,
    DocTypeCreateRequest,
    DocTypeResponse,
    DocTypeUpdateRequest,
    SystemPromptResponse,
    SystemPromptUpdateRequest,
    LLMConfigResponse,
    LLMConfigUpdateRequest,
    WhmcsDefaultsResponse,
    CheckWhmcsCookiesRequest,
    CheckWhmcsCookiesResponse,
    CrawlTicketsRequest,
    CrawlTicketsResponse,
    IngestDocument,
    IngestRequest,
    IngestResponse,
    IngestTicketsToFileResponse,
    IntentCreateRequest,
    IntentResponse,
    IntentUpdateRequest,
    SaveWhmcsCookiesRequest,
    SaveWhmcsCookiesResponse,
    TicketApprovalUpdateRequest,
    WHMCS_COOKIES_KEY,
)
from app.core.auth import verify_admin_api_key
from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.models import AppConfig, DocTypeModel, Intent, Ticket
from app.db.session import get_db
from app.services.archi_config import refresh_cache as refresh_archi_config
from app.services.branding_config import refresh_cache
from app.services.doc_type_service import refresh_doc_type_cache
from app.services.llm_config import refresh_cache as refresh_llm_config
from app.services.llm_gateway import clear_llm_cache
from app.services.query_rewriter import clear_cache as clear_query_rewriter_cache

logger = get_logger(__name__)

router = APIRouter(prefix="/admin", tags=["admin"])


@router.post("/ingest", response_model=IngestResponse)
async def trigger_ingest(
    body: IngestRequest,
    _auth: str = Depends(verify_admin_api_key),
):
    """Trigger ingestion job. Queues documents for processing via Celery."""
    try:
        from worker.tasks import ingest_documents_task

        docs = [
            {
                "url": d.url,
                "title": d.title,
                "raw_text": d.raw_text,
                "raw_html": d.raw_html,
                "content": d.content,
                "doc_type": d.doc_type,
                "effective_date": d.effective_date,
                "last_updated": d.last_updated,
                "product": d.product,
                "region": d.region,
                "metadata": d.metadata,
                "source_file": d.source_file,
            }
            for d in body.documents
        ]
        job = ingest_documents_task.delay(docs)
        return IngestResponse(
            job_id=job.id,
            documents_count=len(docs),
            status="queued",
        )
    except Exception as e:
        logger.error("ingest_trigger_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/ingest-from-source")
async def ingest_from_source(
    _auth: str = Depends(verify_admin_api_key),
    source_dir: str = Query(default="source", description="Path to source directory"),
):
    """Ingest documents from source/ JSON files. Runs synchronously."""
    try:
        from app.services.source_loaders import load_all_docs
        from app.db.session import async_session_factory
        from app.services.ingestion import IngestionService
        import asyncio

        path = Path(source_dir)
        if not path.is_absolute():
            for base in (Path("/app"), Path.cwd()):
                candidate = base / source_dir
                if candidate.exists():
                    path = candidate
                    break
        if not path.exists():
            raise HTTPException(status_code=404, detail=f"Source directory not found: {source_dir}")

        docs = load_all_docs(path)
        if not docs:
            return {"status": "ok", "message": "No documents to ingest", "results": {"ok": 0, "skipped": 0, "error": 0}}

        svc = IngestionService()
        results = {"ok": 0, "skipped": 0, "error": 0}
        for i, doc in enumerate(docs):
            try:
                async with async_session_factory() as session:
                    result = await svc.ingest_document(doc, session)
                    if result:
                        results["ok"] += 1
                    else:
                        results["skipped"] += 1
            except Exception as e:
                results["error"] += 1
                logger.warning("ingest_doc_failed", url=doc.get("url", "")[:80], error=str(e))

        return {"status": "ok", "results": results, "total": len(docs)}
    except HTTPException:
        raise
    except Exception as e:
        logger.error("ingest_from_source_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/save-whmcs-cookies", response_model=SaveWhmcsCookiesResponse)
async def save_whmcs_cookies(
    body: SaveWhmcsCookiesRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Save WHMCS session cookies for later crawl. Paste JSON from browser after manual login."""
    import json

    from app.db.models import generate_uuid

    value = json.dumps(body.session_cookies, ensure_ascii=False)
    result = await db.execute(select(AppConfig).where(AppConfig.key == WHMCS_COOKIES_KEY).limit(1))
    row = result.scalars().one_or_none()
    if row:
        row.value = value
    else:
        row = AppConfig(id=generate_uuid(), key=WHMCS_COOKIES_KEY, value=value)
        db.add(row)
    await db.commit()
    return SaveWhmcsCookiesResponse(status="ok", count=len(body.session_cookies))


@router.post("/check-whmcs-cookies", response_model=CheckWhmcsCookiesResponse)
async def api_check_whmcs_cookies(
    body: CheckWhmcsCookiesRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Check if saved or provided cookies authenticate successfully."""
    session_cookies = body.session_cookies
    if not session_cookies or len(session_cookies) == 0:
        result = await db.execute(select(AppConfig).where(AppConfig.key == WHMCS_COOKIES_KEY).limit(1))
        row = result.scalars().one_or_none()
        if row:
            import json

            try:
                session_cookies = json.loads(row.value)
            except Exception:
                session_cookies = None

    if not session_cookies or len(session_cookies) == 0:
        raise HTTPException(
            status_code=400,
            detail="Save cookies first or send session_cookies in body.",
        )

    s = get_settings()
    effective_base_url = (body.base_url or "").strip() or s.whmcs_base_url or ""
    effective_list_path = (body.list_path or "").strip() or s.whmcs_list_path or "supporttickets.php?filter=1"
    if not effective_base_url:
        raise HTTPException(
            status_code=400,
            detail="Configure WHMCS_BASE_URL in env or provide base_url in request.",
        )

    from app.crawlers.whmcs import check_whmcs_cookies as do_check

    def _run():
        return do_check(
            base_url=effective_base_url.rstrip("/"),
            list_path=effective_list_path,
            session_cookies=session_cookies,
            headless=True,
            timeout_ms=15000,
            debug=body.debug,
        )

    try:
        ok, message, debug_info = await asyncio.to_thread(_run)
        return CheckWhmcsCookiesResponse(ok=ok, message=message, debug=debug_info)
    except Exception as e:
        logger.error("check_whmcs_cookies_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/whmcs-cookies")
async def get_whmcs_cookies(
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Get saved WHMCS cookies (count only, not values)."""
    result = await db.execute(select(AppConfig).where(AppConfig.key == WHMCS_COOKIES_KEY).limit(1))
    row = result.scalars().one_or_none()
    if not row:
        return {"saved": False, "count": 0}
    try:
        import json

        data = json.loads(row.value)
        return {"saved": True, "count": len(data) if isinstance(data, list) else 0}
    except Exception:
        return {"saved": True, "count": 0}


@router.get("/config/whmcs", response_model=WhmcsDefaultsResponse)
async def get_whmcs_defaults(_auth: str = Depends(verify_admin_api_key)):
    """Get WHMCS crawler defaults from config/env (base_url, list_path, login_path)."""
    s = get_settings()
    return WhmcsDefaultsResponse(
        base_url=s.whmcs_base_url or "",
        list_path=s.whmcs_list_path or "supporttickets.php?filter=1",
        login_path=s.whmcs_login_path or "login.php",
    )


@router.post("/crawl-tickets", response_model=CrawlTicketsResponse)
async def crawl_tickets(
    body: CrawlTicketsRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Crawl WHMCS tickets. Uses saved cookies (from save-whmcs-cookies) or inline session_cookies/credentials."""
    session_cookies = body.session_cookies
    if not session_cookies or len(session_cookies) == 0:
        # Load from saved config
        result = await db.execute(select(AppConfig).where(AppConfig.key == WHMCS_COOKIES_KEY).limit(1))
        row = result.scalars().one_or_none()
        if row:
            import json

            try:
                session_cookies = json.loads(row.value)
            except Exception:
                session_cookies = None

    has_cookies = session_cookies and len(session_cookies) > 0
    has_creds = body.username and body.password
    if not has_cookies and not has_creds:
        raise HTTPException(
            status_code=400,
            detail="Save cookies first (Save cookies) or provide username+password.",
        )

    s = get_settings()
    effective_base_url = (body.base_url or "").strip() or s.whmcs_base_url or ""
    effective_list_path = (body.list_path or "").strip() or s.whmcs_list_path or "supporttickets.php?filter=1"
    effective_login_path = (body.login_path or "").strip() or s.whmcs_login_path or "login.php"
    if not effective_base_url:
        raise HTTPException(
            status_code=400,
            detail="Configure WHMCS_BASE_URL in env or provide base_url in request.",
        )

    from app.crawlers.whmcs import WHMCSConfig, crawl_whmcs_tickets
    from app.services.ticket_db import upsert_ticket_from_crawl

    ticket_queue: queue.Queue = queue.Queue()

    async def _save_worker():
        """Save each ticket to DB as soon as crawl finishes."""
        saved = 0
        while True:
            try:
                t = await asyncio.to_thread(ticket_queue.get)
                if t is None:
                    break
                ok = await upsert_ticket_from_crawl(t)
                if ok:
                    saved += 1
            except Exception as e:
                logger.warning("crawl_ticket_save_failed", error=str(e))
        return saved

    def _run_crawl():
        config = WHMCSConfig(
            base_url=effective_base_url.rstrip("/"),
            list_path=effective_list_path,
            login_path=effective_login_path,
            username=body.username,
            password=body.password,
            totp_code=body.totp_code,
            session_cookies=session_cookies,
            headless=True,
        )
        return crawl_whmcs_tickets(config, ticket_queue=ticket_queue)

    try:
        save_task = asyncio.create_task(_save_worker())
        tickets, skipped = await asyncio.to_thread(_run_crawl)
        await save_task
    except Exception as e:
        logger.error("crawl_tickets_failed", error=str(e))
        raise HTTPException(status_code=500, detail=str(e))

    # Crawl only saves to DB. Use ingest-tickets-to-file endpoint to export approved tickets to file.
    return CrawlTicketsResponse(
        status="ok",
        count=len(tickets),
        skipped=skipped,
        saved_to="database",
        tickets=tickets,
    )


@router.patch("/tickets/{ticket_id}/approval")
async def update_ticket_approval(
    ticket_id: str,
    body: TicketApprovalUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Update ticket approval status: pending, approved, rejected."""
    result = await db.execute(select(Ticket).where(Ticket.id == ticket_id).limit(1))
    row = result.scalar_one_or_none()
    if not row:
        result = await db.execute(select(Ticket).where(Ticket.external_id == ticket_id).limit(1))
        row = result.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Ticket not found")
    row.approval_status = body.approval_status
    await db.commit()
    return {"status": "ok", "approval_status": body.approval_status}


@router.post("/ingest-tickets-to-file", response_model=IngestTicketsToFileResponse)
async def ingest_tickets_to_file(
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Export approved tickets (approval_status=approved) to source/sample_conversations.json. Only approved tickets are used."""
    from datetime import datetime, timezone

    from app.services.ticket_sync import _resolve_source_dir

    result = await db.execute(
        select(Ticket).where(Ticket.approval_status == "approved").order_by(Ticket.updated_at.desc())
    )
    rows = result.scalars().all()

    source_dir = _resolve_source_dir()
    source_dir.mkdir(parents=True, exist_ok=True)
    out_path = source_dir / "sample_conversations.json"

    def _row_to_dict(r):
        meta = dict(r.ticket_metadata or {})
        return {
            "id": r.id,
            "external_id": r.external_id,
            "subject": r.subject,
            "description": r.description or "",
            "status": r.status,
            "priority": r.priority,
            "client_id": r.client_id,
            "email": r.email,
            "name": r.name,
            "detail_url": meta.get("detail_url"),
            "metadata": meta,
        }

    data = {
        "source": "whmcs",
        "ingested_at": datetime.now(timezone.utc).isoformat(),
        "conversations_count": len(rows),
        "conversations": [_row_to_dict(r) for r in rows],
    }
    try:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        logger.info("ingest_tickets_to_file_ok", path=str(out_path), count=len(rows))
    except OSError as e:
        logger.warning("ingest_tickets_to_file_failed", path=str(out_path), error=str(e))
        raise HTTPException(status_code=500, detail=f"Failed to write file: {e}")

    return IngestTicketsToFileResponse(
        status="ok",
        path=str(out_path),
        count=len(rows),
    )


# --- Branding config (prompts, intents) ---


@router.get("/config/llm", response_model=LLMConfigResponse)
async def get_llm_config(_auth: str = Depends(verify_admin_api_key)):
    """Get current LLM config (model, token, URL) from cache/DB."""
    from app.services.llm_config import (
        get_llm_api_key,
        get_llm_base_url,
        get_llm_fallback_model,
        get_llm_model,
    )
    return LLMConfigResponse(
        llm_model=get_llm_model(),
        llm_fallback_model=get_llm_fallback_model(),
        llm_api_key=get_llm_api_key(),
        llm_base_url=get_llm_base_url(),
    )


@router.put("/config/llm", response_model=LLMConfigResponse)
async def update_llm_config(
    body: LLMConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Update LLM config. Only provided fields are updated."""
    from app.db.models import generate_uuid

    keys_to_update = []
    if body.llm_model is not None:
        keys_to_update.append(("llm_model", body.llm_model))
    if body.llm_fallback_model is not None:
        keys_to_update.append(("llm_fallback_model", body.llm_fallback_model))
    if body.llm_api_key is not None:
        keys_to_update.append(("llm_api_key", body.llm_api_key))
    if body.llm_base_url is not None:
        keys_to_update.append(("llm_base_url", body.llm_base_url))

    for key, value in keys_to_update:
        result = await db.execute(select(AppConfig).where(AppConfig.key == key).limit(1))
        row = result.scalars().one_or_none()
        if row:
            row.value = value
        else:
            row = AppConfig(id=generate_uuid(), key=key, value=value)
            db.add(row)
    await db.flush()
    await refresh_cache(db)
    await refresh_llm_config(db)

    from app.services.llm_config import (
        get_llm_api_key,
        get_llm_base_url,
        get_llm_fallback_model,
        get_llm_model,
    )
    return LLMConfigResponse(
        llm_model=get_llm_model(),
        llm_fallback_model=get_llm_fallback_model(),
        llm_api_key=get_llm_api_key(),
        llm_base_url=get_llm_base_url(),
    )


@router.get("/config/archi", response_model=ArchiConfigResponse)
async def get_archi_config(_auth: str = Depends(verify_admin_api_key)):
    """Get archi v3 feature flags from cache/DB."""
    from app.services.archi_config import (
        get_debug_llm_calls,
        get_decision_router_use_llm,
        get_doc_type_classifier_enabled,
        get_evidence_evaluator_enabled,
        get_evidence_quality_llm_v2,
        get_evidence_quality_use_llm,
        get_final_polish_enabled,
        get_language_detect_enabled,
        get_llm_model_economy,
        get_llm_task_aware_routing_enabled,
        get_retrieval_doc_type_use_llm,
        get_self_critic_enabled,
    )
    return ArchiConfigResponse(
        language_detect_enabled=get_language_detect_enabled(),
        decision_router_use_llm=get_decision_router_use_llm(),
        evidence_evaluator_enabled=get_evidence_evaluator_enabled(),
        evidence_quality_use_llm=get_evidence_quality_use_llm(),
        evidence_quality_llm_v2=get_evidence_quality_llm_v2(),
        debug_llm_calls=get_debug_llm_calls(),
        self_critic_enabled=get_self_critic_enabled(),
        final_polish_enabled=get_final_polish_enabled(),
        doc_type_classifier_enabled=get_doc_type_classifier_enabled(),
        retrieval_doc_type_use_llm=get_retrieval_doc_type_use_llm(),
        llm_model_economy=get_llm_model_economy(),
        llm_task_aware_routing_enabled=get_llm_task_aware_routing_enabled(),
    )


@router.put("/config/archi", response_model=ArchiConfigResponse)
async def update_archi_config(
    body: ArchiConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Update archi v3 feature flags. Only provided fields are updated."""
    from app.db.models import generate_uuid

    updates: list[tuple[str, Any, bool]] = [
        ("language_detect_enabled", body.language_detect_enabled, True),
        ("decision_router_use_llm", body.decision_router_use_llm, True),
        ("evidence_evaluator_enabled", body.evidence_evaluator_enabled, True),
        ("evidence_quality_use_llm", body.evidence_quality_use_llm, True),
        ("evidence_quality_llm_v2", body.evidence_quality_llm_v2, True),
        ("debug_llm_calls", body.debug_llm_calls, True),
        ("self_critic_enabled", body.self_critic_enabled, True),
        ("final_polish_enabled", body.final_polish_enabled, True),
        ("doc_type_classifier_enabled", body.doc_type_classifier_enabled, True),
        ("retrieval_doc_type_use_llm", body.retrieval_doc_type_use_llm, True),
        ("llm_model_economy", body.llm_model_economy, False),
        ("llm_task_aware_routing_enabled", body.llm_task_aware_routing_enabled, True),
    ]
    for key, value, is_bool in updates:
        if value is None:
            continue
        str_val = "true" if (is_bool and value) else ("false" if is_bool else str(value))
        result = await db.execute(select(AppConfig).where(AppConfig.key == key).limit(1))
        row = result.scalars().one_or_none()
        if row:
            row.value = str_val
        else:
            row = AppConfig(id=generate_uuid(), key=key, value=str_val)
            db.add(row)
    await db.flush()
    await refresh_archi_config(db)

    from app.services.archi_config import (
        get_debug_llm_calls,
        get_decision_router_use_llm,
        get_doc_type_classifier_enabled,
        get_evidence_evaluator_enabled,
        get_evidence_quality_llm_v2,
        get_evidence_quality_use_llm,
        get_final_polish_enabled,
        get_language_detect_enabled,
        get_llm_model_economy,
        get_llm_task_aware_routing_enabled,
        get_retrieval_doc_type_use_llm,
        get_self_critic_enabled,
    )
    return ArchiConfigResponse(
        language_detect_enabled=get_language_detect_enabled(),
        decision_router_use_llm=get_decision_router_use_llm(),
        evidence_evaluator_enabled=get_evidence_evaluator_enabled(),
        evidence_quality_use_llm=get_evidence_quality_use_llm(),
        evidence_quality_llm_v2=get_evidence_quality_llm_v2(),
        debug_llm_calls=get_debug_llm_calls(),
        self_critic_enabled=get_self_critic_enabled(),
        final_polish_enabled=get_final_polish_enabled(),
        doc_type_classifier_enabled=get_doc_type_classifier_enabled(),
        retrieval_doc_type_use_llm=get_retrieval_doc_type_use_llm(),
        llm_model_economy=get_llm_model_economy(),
        llm_task_aware_routing_enabled=get_llm_task_aware_routing_enabled(),
    )


@router.post("/config/refresh-cache")
async def refresh_config_cache(
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Refresh in-memory cache for system prompt, intents, doc types, LLM config, and archi config from DB."""
    await refresh_cache(db)
    await refresh_doc_type_cache(db)
    await refresh_llm_config(db)
    await refresh_archi_config(db)
    return {"status": "ok", "message": "Cache refreshed"}


@router.post("/conversations/refresh-cache")
async def refresh_conversation_cache(_auth: str = Depends(verify_admin_api_key)):
    """Clear conversation-related runtime caches used by chat flows."""
    query_rewriter_result, llm_cache_result = await asyncio.gather(
        clear_query_rewriter_cache(),
        clear_llm_cache(),
    )
    return {
        "status": "ok",
        "message": "Conversation cache refreshed",
        "query_rewriter": query_rewriter_result,
        "llm_cache": llm_cache_result,
    }


@router.post("/config/auto-generate-from-domain", response_model=AutoGenerateBrandingResponse)
async def auto_generate_branding_from_domain(
    body: AutoGenerateBrandingRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Fetch website content, analyze with AI, and save persona, prompt_domain, custom_prompt_rules to DB."""
    from app.db.models import generate_uuid

    from app.services.branding_auto_generator import generate_branding_from_domain

    try:
        result = await generate_branding_from_domain(body.url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.error("auto_generate_branding_failed", url=body.url, error=str(e))
        raise HTTPException(status_code=502, detail=f"Failed to generate: {e}") from e

    keys_to_save = [
        ("system_prompt", result["persona"]),
        ("prompt_domain", result["prompt_domain"]),
        ("custom_prompt_rules", result["custom_prompt_rules"]),
    ]
    for key, value in keys_to_save:
        existing = await db.execute(select(AppConfig).where(AppConfig.key == key).limit(1))
        row = existing.scalars().one_or_none()
        if row:
            row.value = value
        else:
            db.add(AppConfig(id=generate_uuid(), key=key, value=value))
    await db.flush()
    await refresh_cache(db)

    return AutoGenerateBrandingResponse(
        status="ok",
        persona=result["persona"],
        prompt_domain=result["prompt_domain"],
        custom_prompt_rules=result["custom_prompt_rules"],
        app_name=result.get("app_name", ""),
    )


@router.get("/config/system-prompt", response_model=SystemPromptResponse)
async def get_system_prompt_config(_auth: str = Depends(verify_admin_api_key)):
    """Get current system prompt (from DB or fallback). Never 404."""
    from app.services.branding_config import get_system_prompt

    return SystemPromptResponse(value=get_system_prompt())


@router.put("/config/system-prompt", response_model=SystemPromptResponse)
async def update_system_prompt_config(
    body: SystemPromptUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Update system prompt. Stored in DB, cache refreshed."""
    from app.db.models import generate_uuid

    result = await db.execute(select(AppConfig).where(AppConfig.key == "system_prompt").limit(1))
    row = result.scalars().one_or_none()
    if row:
        row.value = body.value
    else:
        row = AppConfig(id=generate_uuid(), key="system_prompt", value=body.value)
        db.add(row)
    await db.flush()
    await refresh_cache(db)
    return SystemPromptResponse(value=body.value)


@router.get("/config/{key}", response_model=AppConfigResponse)
async def get_config(
    key: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Get config value by key (e.g. system_prompt)."""
    result = await db.execute(select(AppConfig).where(AppConfig.key == key).limit(1))
    row = result.scalars().one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail=f"Config key not found: {key}")
    return AppConfigResponse(key=row.key, value=row.value)


@router.put("/config/{key}", response_model=AppConfigResponse)
async def update_config(
    key: str,
    body: AppConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Update config value. Creates if key does not exist."""
    result = await db.execute(select(AppConfig).where(AppConfig.key == key).limit(1))
    row = result.scalars().one_or_none()
    if row:
        row.value = body.value
    else:
        from app.db.models import generate_uuid
        row = AppConfig(id=generate_uuid(), key=key, value=body.value)
        db.add(row)
    await db.flush()
    await refresh_cache(db)
    await refresh_llm_config(db)
    return AppConfigResponse(key=row.key, value=row.value)


@router.get("/intents", response_model=list[IntentResponse])
async def list_intents(
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """List all intents ordered by sort_order."""
    result = await db.execute(select(Intent).order_by(Intent.sort_order))
    rows = result.scalars().all()
    return [
        IntentResponse(id=r.id, key=r.key, patterns=r.patterns, answer=r.answer, enabled=r.enabled, sort_order=r.sort_order)
        for r in rows
    ]


@router.post("/intents", response_model=IntentResponse)
async def create_intent(
    body: IntentCreateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Create a new intent."""
    result = await db.execute(select(Intent).where(Intent.key == body.key).limit(1))
    if result.scalars().one_or_none():
        raise HTTPException(status_code=409, detail=f"Intent key already exists: {body.key}")
    from app.db.models import generate_uuid
    intent = Intent(
        id=generate_uuid(),
        key=body.key,
        patterns=body.patterns,
        answer=body.answer,
        enabled=body.enabled,
        sort_order=body.sort_order,
    )
    db.add(intent)
    await db.flush()
    await refresh_cache(db)
    return IntentResponse(id=intent.id, key=intent.key, patterns=intent.patterns, answer=intent.answer, enabled=intent.enabled, sort_order=intent.sort_order)


@router.put("/intents/{intent_id}", response_model=IntentResponse)
async def update_intent(
    intent_id: str,
    body: IntentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Update intent by id."""
    result = await db.execute(select(Intent).where(Intent.id == intent_id).limit(1))
    intent = result.scalars().one_or_none()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")
    if body.patterns is not None:
        intent.patterns = body.patterns
    if body.answer is not None:
        intent.answer = body.answer
    if body.enabled is not None:
        intent.enabled = body.enabled
    if body.sort_order is not None:
        intent.sort_order = body.sort_order
    await db.flush()
    await refresh_cache(db)
    return IntentResponse(id=intent.id, key=intent.key, patterns=intent.patterns, answer=intent.answer, enabled=intent.enabled, sort_order=intent.sort_order)


@router.delete("/intents/{intent_id}")
async def delete_intent(
    intent_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Delete intent by id."""
    result = await db.execute(select(Intent).where(Intent.id == intent_id).limit(1))
    intent = result.scalars().one_or_none()
    if not intent:
        raise HTTPException(status_code=404, detail="Intent not found")
    await db.delete(intent)
    await db.flush()
    await refresh_cache(db)
    return {"status": "ok", "message": "Intent deleted"}


# --- Doc types (user-managed document type catalog) ---


@router.get("/doc-types", response_model=list[DocTypeResponse])
async def list_doc_types(
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """List all doc types ordered by sort_order."""
    result = await db.execute(select(DocTypeModel).order_by(DocTypeModel.sort_order))
    rows = result.scalars().all()
    return [
        DocTypeResponse(
            id=r.id,
            key=r.key,
            label=r.label,
            description=r.description,
            enabled=r.enabled,
            sort_order=r.sort_order,
        )
        for r in rows
    ]


@router.post("/doc-types", response_model=DocTypeResponse)
async def create_doc_type(
    body: DocTypeCreateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Create a new doc type."""
    from app.db.models import generate_uuid

    result = await db.execute(select(DocTypeModel).where(DocTypeModel.key == body.key).limit(1))
    if result.scalars().one_or_none():
        raise HTTPException(status_code=409, detail=f"Doc type key already exists: {body.key}")

    key_safe = body.key.strip().lower().replace(" ", "_")
    doc_type = DocTypeModel(
        id=generate_uuid(),
        key=key_safe,
        label=body.label,
        description=body.description,
        enabled=body.enabled,
        sort_order=body.sort_order,
    )
    db.add(doc_type)
    await db.commit()
    await db.refresh(doc_type)
    await refresh_doc_type_cache(db)
    return DocTypeResponse(
        id=doc_type.id,
        key=doc_type.key,
        label=doc_type.label,
        description=doc_type.description,
        enabled=doc_type.enabled,
        sort_order=doc_type.sort_order,
    )


@router.put("/doc-types/{doc_type_id}", response_model=DocTypeResponse)
async def update_doc_type(
    doc_type_id: str,
    body: DocTypeUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Update doc type by id."""
    result = await db.execute(select(DocTypeModel).where(DocTypeModel.id == doc_type_id).limit(1))
    doc_type = result.scalars().one_or_none()
    if not doc_type:
        raise HTTPException(status_code=404, detail="Doc type not found")

    if body.label is not None:
        doc_type.label = body.label
    if body.description is not None:
        doc_type.description = body.description
    if body.enabled is not None:
        doc_type.enabled = body.enabled
    if body.sort_order is not None:
        doc_type.sort_order = body.sort_order

    await db.commit()
    await db.refresh(doc_type)
    await refresh_doc_type_cache(db)
    return DocTypeResponse(
        id=doc_type.id,
        key=doc_type.key,
        label=doc_type.label,
        description=doc_type.description,
        enabled=doc_type.enabled,
        sort_order=doc_type.sort_order,
    )


@router.delete("/doc-types/{doc_type_id}")
async def delete_doc_type(
    doc_type_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_admin_api_key),
):
    """Delete doc type by id."""
    result = await db.execute(select(DocTypeModel).where(DocTypeModel.id == doc_type_id).limit(1))
    doc_type = result.scalars().one_or_none()
    if not doc_type:
        raise HTTPException(status_code=404, detail="Doc type not found")
    await db.delete(doc_type)
    await db.commit()
    await refresh_doc_type_cache(db)
    return {"status": "ok", "message": "Doc type deleted"}
