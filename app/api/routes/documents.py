"""Documents CRUD API routes."""

import uuid
from datetime import datetime

import httpx
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.schemas import (
    CrawlWebsiteRequest,
    CrawlWebsiteResponse,
    CrawledPage,
    DocumentCreateRequest,
    DocumentListResponse,
    DocumentResponse,
    DocumentUpdateRequest,
    FetchFromUrlRequest,
    FetchFromUrlResponse,
)
from app.core.auth import verify_api_key
from app.db.models import Document, Chunk
from app.db.session import get_db
from app.services.source_sync import (
    CUSTOM_DOCS_FILE,
    sync_document_create,
    sync_document_delete,
    sync_document_update,
)

router = APIRouter(prefix="/documents", tags=["documents"])


@router.post("/fetch-from-url", response_model=FetchFromUrlResponse)
async def fetch_content_from_url(
    body: FetchFromUrlRequest,
    _auth: str = Depends(verify_api_key),
):
    """Fetch webpage content from URL. Returns title and extracted text for document creation."""
    from app.services.url_fetcher import fetch_content_from_url as do_fetch

    try:
        result = do_fetch(body.url)
        return FetchFromUrlResponse(
            title=result["title"],
            content=result["content"],
            raw_html=result.get("raw_html"),
        )
    except httpx.HTTPStatusError as e:
        raise HTTPException(status_code=502, detail=f"URL returned {e.response.status_code}")
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Cannot fetch URL: {str(e)}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/crawl-website", response_model=CrawlWebsiteResponse)
async def crawl_website(
    body: CrawlWebsiteRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Crawl entire website from seed URL and optionally ingest into knowledge base."""
    import asyncio
    from app.services.web_crawler import crawl_website as do_crawl
    from app.services.ingestion import IngestionService

    try:
        docs = await asyncio.to_thread(
            do_crawl,
            body.url,
            max_pages=body.max_pages,
            max_depth=body.max_depth,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Crawl failed: {str(e)}")

    pages: list[CrawledPage] = [
        CrawledPage(url=d["url"], title=d["title"], doc_type=d["doc_type"])
        for d in docs
    ]

    ingested = 0
    if body.ingest and docs:
        svc = IngestionService()
        for doc in docs:
            doc_id = await svc.ingest_document(doc, db)
            if doc_id:
                ingested += 1
                sync_document_create(
                    source_url=doc["source_url"],
                    title=doc["title"],
                    content=doc.get("content", doc.get("raw_text", "")),
                )

    return CrawlWebsiteResponse(
        status="ok",
        pages_crawled=len(docs),
        pages_ingested=ingested,
        pages=pages,
    )


@router.get("", response_model=DocumentListResponse)
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    doc_type: str | None = Query(None, description="Filter by type: policy, faq, howto, pricing, tos, other"),
    q: str | None = Query(None, description="Search in title, source_url"),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """List documents with pagination and filters."""
    offset = (page - 1) * page_size
    base = select(Document)
    count_base = select(func.count()).select_from(Document)

    if doc_type:
        base = base.where(Document.doc_type == doc_type)
        count_base = count_base.where(Document.doc_type == doc_type)
    if q and q.strip():
        search = f"%{q.strip()}%"
        base = base.where(
            (Document.title.ilike(search)) | (Document.source_url.ilike(search))
        )
        count_base = count_base.where(
            (Document.title.ilike(search)) | (Document.source_url.ilike(search))
        )

    count_result = await db.execute(count_base)
    total = count_result.scalar() or 0

    result = await db.execute(
        base.order_by(Document.updated_at.desc()).offset(offset).limit(page_size)
    )
    docs = result.scalars().all()

    # Get chunk count per document
    items = []
    for d in docs:
        chunk_count = await db.execute(
            select(func.count()).select_from(Chunk).where(Chunk.document_id == d.id)
        )
        items.append(
            DocumentResponse(
                id=d.id,
                title=d.title,
                source_url=d.source_url,
                doc_type=d.doc_type,
                effective_date=d.effective_date,
                chunks_count=chunk_count.scalar() or 0,
                source_file=d.source_file,
                metadata=d.doc_metadata,
                created_at=d.created_at,
                updated_at=d.updated_at,
            )
        )

    return DocumentListResponse(items=items, total=total, page=page, page_size=page_size)


@router.get("/{document_id}", response_model=DocumentResponse)
async def get_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Get document details."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    chunk_count = await db.execute(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == doc.id)
    )
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        source_url=doc.source_url,
        doc_type=doc.doc_type,
        effective_date=doc.effective_date,
        chunks_count=chunk_count.scalar() or 0,
        source_file=doc.source_file,
        metadata=doc.doc_metadata,
        raw_content=doc.raw_content,
        cleaned_content=doc.cleaned_content,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.post("/upload", response_model=DocumentResponse, status_code=201)
async def upload_document(
    file: UploadFile = File(...),
    title: str | None = Form(None),
    doc_type: str = Form("other"),
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Upload .txt, .md, or .pdf file and ingest into knowledge base."""
    from app.services.file_parser import extract_text_from_file
    from app.services.ingestion import IngestionService

    filename = file.filename or "upload"
    content = await file.read()
    if not content:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        raw_text = extract_text_from_file(content, filename, file.content_type)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    if len(raw_text.strip()) < 50:
        raise HTTPException(
            status_code=400,
            detail="File content too short (min 50 chars). Check encoding or file content.",
        )

    url = f"file://{uuid.uuid4().hex[:8]}-{filename}"
    doc_title = (title or filename).strip() or "Untitled"
    doc_dict = {
        "url": url,
        "source_url": url,
        "title": doc_title,
        "raw_text": raw_text,
        "doc_type": doc_type,
        "source_file": CUSTOM_DOCS_FILE,
    }

    svc = IngestionService()
    document_id = await svc.ingest_document(doc_dict, db)
    if not document_id:
        raise HTTPException(status_code=400, detail="Document skipped (duplicate or invalid)")

    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one()

    if not doc.source_file:
        doc.source_file = CUSTOM_DOCS_FILE
        await db.commit()
        await db.refresh(doc)

    sync_document_create(
        source_url=doc.source_url,
        title=doc.title,
        content=doc.cleaned_content or doc.raw_content or "",
    )

    chunk_count = await db.execute(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == doc.id)
    )
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        source_url=doc.source_url,
        doc_type=doc.doc_type,
        effective_date=doc.effective_date,
        chunks_count=chunk_count.scalar() or 0,
        source_file=doc.source_file,
        metadata=doc.doc_metadata,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.post("", response_model=DocumentResponse, status_code=201)
async def create_document(
    body: DocumentCreateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Create new document via ingestion pipeline."""
    from app.services.ingestion import IngestionService

    doc_dict = {
        "url": body.url,
        "title": body.title,
        "raw_text": body.raw_text,
        "raw_html": body.raw_html,
        "content": body.content,
        "doc_type": body.doc_type,
        "effective_date": body.effective_date,
        "last_updated": body.last_updated,
        "product": body.product,
        "region": body.region,
        "metadata": body.metadata,
        "source_file": body.source_file,
    }
    svc = IngestionService()
    document_id = await svc.ingest_document(doc_dict, db)
    if not document_id:
        raise HTTPException(status_code=400, detail="Document skipped (duplicate or invalid)")

    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one()

    # Assign source_file for docs created via admin panel
    if not doc.source_file:
        doc.source_file = CUSTOM_DOCS_FILE
        await db.commit()
        await db.refresh(doc)

    # Sync to source JSON
    sync_document_create(
        source_url=doc.source_url,
        title=doc.title,
        content=doc.cleaned_content or doc.raw_content or "",
    )

    chunk_count = await db.execute(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == doc.id)
    )
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        source_url=doc.source_url,
        doc_type=doc.doc_type,
        effective_date=doc.effective_date,
        chunks_count=chunk_count.scalar() or 0,
        source_file=doc.source_file,
        metadata=doc.doc_metadata,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.patch("/{document_id}", response_model=DocumentResponse)
async def update_document(
    document_id: str,
    body: DocumentUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Update document metadata (title, doc_type, effective_date, metadata)."""
    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    if body.title is not None:
        doc.title = body.title
    if body.doc_type is not None:
        doc.doc_type = body.doc_type
    if body.effective_date is not None:
        try:
            doc.effective_date = datetime.fromisoformat(body.effective_date.replace("Z", "+00:00"))
        except ValueError:
            doc.effective_date = None
    if body.metadata is not None:
        doc.doc_metadata = body.metadata

    await db.commit()
    await db.refresh(doc)

    # Sync changed fields back to source JSON
    sync_document_update(
        source_url=doc.source_url,
        source_file=doc.source_file,
        title=body.title,
        cleaned_content=doc.cleaned_content,
    )

    chunk_count = await db.execute(
        select(func.count()).select_from(Chunk).where(Chunk.document_id == doc.id)
    )
    return DocumentResponse(
        id=doc.id,
        title=doc.title,
        source_url=doc.source_url,
        doc_type=doc.doc_type,
        effective_date=doc.effective_date,
        chunks_count=chunk_count.scalar() or 0,
        source_file=doc.source_file,
        metadata=doc.doc_metadata,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.delete("/{document_id}", status_code=204)
async def delete_document(
    document_id: str,
    db: AsyncSession = Depends(get_db),
    _auth: str = Depends(verify_api_key),
):
    """Delete document and chunks (cascade). Also removes from OpenSearch/Qdrant."""
    from sqlalchemy.orm import selectinload
    import asyncio

    result = await db.execute(
        select(Document).where(Document.id == document_id).options(selectinload(Document.chunks))
    )
    doc = result.scalar_one_or_none()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove from vector store (OpenSearch/Qdrant) if present
    try:
        from app.search.opensearch_client import OpenSearchClient
        from app.search.qdrant_client import QdrantSearchClient

        os_client = OpenSearchClient()
        qdrant = QdrantSearchClient()
        for chunk in doc.chunks:
            try:
                await os_client.delete_chunk(chunk.id)
            except Exception:
                pass
            try:
                await asyncio.to_thread(qdrant.delete_chunk, chunk.id)
            except Exception:
                pass
    except Exception:
        pass  # Continue with DB delete even if vector store fails

    # Sync removal to source JSON before deleting from DB
    sync_document_delete(source_url=doc.source_url, source_file=doc.source_file)

    await db.delete(doc)
    await db.commit()
