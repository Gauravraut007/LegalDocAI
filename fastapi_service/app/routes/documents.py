"""Documents API: upload, list, get, delete, SSE progress, text, chunks."""
from __future__ import annotations

import asyncio
import json
import os
import uuid
from typing import AsyncGenerator, Optional

import structlog
from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import PlainTextResponse, Response, StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.document import (
    Chunk as ChunkRow,
    DocStatus,
    DocType,
    Document,
    DocumentVersion,
    IngestJob,
    JobState,
    VersionKind,
)
from app.security import AuthenticatedUser, get_current_user
from app.schemas.document import (
    ChunkOut,
    DocumentDetail,
    DocumentList,
    DocumentSummary,
    DocumentUploadResponse,
    IngestJobOut,
    PaginatedChunks,
)
from app.utils.events import _channel, _latest_key  # noqa: PLC2701 - intentional import
from app.utils.file_handler import FileHandler, UploadValidationError

logger = structlog.get_logger("documents")
router = APIRouter(
    prefix="/api/v1/documents",
    tags=["documents"],
    dependencies=[Depends(get_current_user)],
)


def _ensure_access(user: AuthenticatedUser, doc: Document) -> None:
    if not user.can_access_document(doc.owner_user_id, doc.workspace_id):
        raise HTTPException(status_code=403, detail="forbidden")


# =========================================================================
# Helpers
# =========================================================================
async def _latest_job(session: AsyncSession, document_id: uuid.UUID) -> Optional[IngestJob]:
    res = await session.execute(
        select(IngestJob)
        .where(IngestJob.document_id == document_id)
        .order_by(IngestJob.created_at.desc())
        .limit(1)
    )
    return res.scalars().first()


def _serialise_detail(doc: Document, job: Optional[IngestJob]) -> DocumentDetail:
    base = DocumentDetail.model_validate(doc, from_attributes=True)
    base.latest_job = (
        IngestJobOut.model_validate(job, from_attributes=True) if job else None
    )
    return base


# =========================================================================
# POST /upload
# =========================================================================
@router.post(
    "/upload",
    response_model=DocumentUploadResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def upload_document(
    file: UploadFile = File(...),
    workspace_id: Optional[uuid.UUID] = Form(None),
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> DocumentUploadResponse:
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename is required")
    if workspace_id is not None and workspace_id not in user.workspace_ids and not user.is_staff:
        raise HTTPException(status_code=403, detail="workspace not accessible")
    owner_user_id = user.user_id

    document_id = uuid.uuid4()
    handler = FileHandler()
    try:
        stored = await handler.stream_upload_to_disk(
            file,
            owner_id=owner_user_id,
            document_id=document_id,
            max_bytes=settings.max_file_size_bytes,
        )
    except UploadValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("upload_failed", error=str(exc))
        raise HTTPException(status_code=500, detail="upload failed") from exc

    # Dedupe by (owner, sha256).
    dup_q = await session.execute(
        select(Document).where(
            Document.owner_user_id == owner_user_id,
            Document.sha256 == stored.sha256,
        )
    )
    existing = dup_q.scalars().first()
    if existing:
        # Remove the freshly written copy under this new doc_id.
        await asyncio.to_thread(handler.remove_document_dir, owner_user_id, document_id)
        latest = await _latest_job(session, existing.id)
        return DocumentUploadResponse(
            document_id=existing.id,
            ingest_job_id=latest.id if latest else uuid.uuid4(),
            status=existing.status.value if hasattr(existing.status, "value") else str(existing.status),
            deduplicated=True,
            message="document already exists for this owner",
            original_filename=existing.original_filename,
            size_bytes=existing.size_bytes,
            sha256=existing.sha256,
        )

    doc = Document(
        id=document_id,
        owner_user_id=owner_user_id,
        workspace_id=workspace_id,
        original_filename=file.filename,
        stored_path=stored.stored_path,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        doc_type=DocType.unknown,
        status=DocStatus.uploaded,
    )
    job = IngestJob(document_id=document_id, state=JobState.queued, progress_pct=0)
    session.add(doc)
    session.add(job)
    await session.commit()
    await session.refresh(job)

    # Fire-and-forget the Celery chain.
    from app.workers.tasks import launch_pipeline

    try:
        chain_id = await asyncio.to_thread(launch_pipeline, document_id)
        job.celery_task_id = chain_id
        await session.commit()
    except Exception as exc:  # noqa: BLE001
        logger.exception("celery_dispatch_failed", error=str(exc))
        # Don't fail the upload; the doc is persisted and can be re-queued later.

    return DocumentUploadResponse(
        document_id=document_id,
        ingest_job_id=job.id,
        status=DocStatus.uploaded.value,
        deduplicated=False,
        message="upload accepted; processing started",
        original_filename=file.filename,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
    )


# =========================================================================
# GET /
# =========================================================================
@router.get("/", response_model=DocumentList)
async def list_documents(
    status_filter: Optional[DocStatus] = Query(None, alias="status"),
    doc_type: Optional[DocType] = Query(None),
    workspace_id: Optional[uuid.UUID] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> DocumentList:
    from sqlalchemy import or_

    if user.is_staff:
        stmt = select(Document)
    else:
        clauses = [Document.owner_user_id == user.user_id]
        if user.workspace_ids:
            clauses.append(Document.workspace_id.in_(user.workspace_ids))
        stmt = select(Document).where(or_(*clauses))
    if workspace_id is not None:
        stmt = stmt.where(Document.workspace_id == workspace_id)
    if status_filter is not None:
        stmt = stmt.where(Document.status == status_filter)
    if doc_type is not None:
        stmt = stmt.where(Document.doc_type == doc_type)

    total_stmt = select(func.count()).select_from(stmt.subquery())
    total = (await session.execute(total_stmt)).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(Document.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    return DocumentList(
        items=[DocumentSummary.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


# =========================================================================
# GET /{id}
# =========================================================================
@router.get("/{document_id}", response_model=DocumentDetail)
async def get_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> DocumentDetail:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    _ensure_access(user, doc)
    job = await _latest_job(session, document_id)
    return _serialise_detail(doc, job)


# =========================================================================
# DELETE /{id}
# =========================================================================
@router.delete(
    "/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_document(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    from app.ai.vectorstore import FaissVectorStore

    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    _ensure_access(user, doc)
    owner_id = doc.owner_user_id
    await session.delete(doc)
    await session.commit()

    handler = FileHandler()
    await asyncio.to_thread(handler.remove_document_dir, owner_id, document_id)
    await asyncio.to_thread(FaissVectorStore().delete_index, document_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =========================================================================
# GET /{id}/text
# =========================================================================
@router.get("/{document_id}/text", response_class=PlainTextResponse)
async def get_document_text(
    document_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> str:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    _ensure_access(user, doc)
    res = await session.execute(
        select(DocumentVersion).where(
            DocumentVersion.document_id == document_id,
            DocumentVersion.kind == VersionKind.ocr_text,
        )
    )
    ver = res.scalars().first()
    if ver is None or not os.path.exists(ver.storage_path):
        raise HTTPException(status_code=404, detail="OCR text not available yet")
    return await asyncio.to_thread(_read_text, ver.storage_path)


def _read_text(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


# =========================================================================
# GET /{id}/chunks
# =========================================================================
@router.get("/{document_id}/chunks", response_model=PaginatedChunks)
async def get_document_chunks(
    document_id: uuid.UUID,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> PaginatedChunks:
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    _ensure_access(user, doc)
    stmt = select(ChunkRow).where(ChunkRow.document_id == document_id)
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(ChunkRow.ordinal)
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return PaginatedChunks(
        items=[ChunkOut.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


# =========================================================================
# GET /{id}/events  (SSE)
# =========================================================================
@router.get("/{document_id}/events")
async def stream_events(
    document_id: uuid.UUID,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> StreamingResponse:
    """Server-Sent Events stream of pipeline progress."""
    doc = await session.get(Document, document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="document not found")
    _ensure_access(user, doc)

    async def event_generator() -> AsyncGenerator[bytes, None]:
        from redis import asyncio as aioredis

        client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        channel = _channel(document_id)
        latest_key = _latest_key(document_id)

        # Replay latest snapshot first, if any.
        snapshot = await client.get(latest_key)
        if snapshot:
            yield f"event: snapshot\ndata: {snapshot}\n\n".encode("utf-8")
            try:
                payload = json.loads(snapshot)
                if payload.get("stage") in {"ready", "failed"}:
                    yield b"event: end\ndata: {}\n\n"
                    await client.aclose()
                    return
            except Exception:  # noqa: BLE001
                pass

        pubsub = client.pubsub()
        await pubsub.subscribe(channel)
        try:
            while True:
                if await request.is_disconnected():
                    break
                msg = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=15.0
                )
                if msg is None:
                    # Keep-alive comment to defeat proxy buffering.
                    yield b": keep-alive\n\n"
                    continue
                data = msg.get("data")
                if not data:
                    continue
                yield f"event: progress\ndata: {data}\n\n".encode("utf-8")
                try:
                    payload = json.loads(data)
                    if payload.get("stage") in {"ready", "failed"}:
                        yield b"event: end\ndata: {}\n\n"
                        break
                except Exception:  # noqa: BLE001
                    continue
        finally:
            try:
                await pubsub.unsubscribe(channel)
                await pubsub.close()
            except Exception:  # noqa: BLE001
                pass
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
