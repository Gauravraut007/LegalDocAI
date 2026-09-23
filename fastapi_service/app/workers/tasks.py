"""Celery pipeline for document ingestion (Phase 2).

Stages (all idempotent + resumable; each publishes a Redis pub/sub event):
    1. validate_document      [queue: default]
    2. ocr_document           [queue: ocr]
    3. chunk_document         [queue: default]
    4. embed_document         [queue: embed]
    5. index_document         [queue: default]

The pipeline is launched as a Celery ``chain`` from the upload route.
Per-document Postgres advisory locks prevent two workers from racing on
the same document.
"""
from __future__ import annotations

import logging
import os
import time
import uuid
from datetime import datetime, timezone
from typing import Optional

import structlog
from celery import Celery, chain
from celery.utils.log import get_task_logger
from sqlalchemy import select

from app.config import settings
from app.database import SyncSessionLocal, sync_engine
from app.models.document import (
    Chunk as ChunkRow,
    DocStatus,
    DocType,
    Document,
    DocumentVersion,
    EmbeddingsMeta,
    IngestJob,
    JobState,
    VersionKind,
)
from app.utils import metrics
from app.utils.events import build_event, publish_sync
from app.utils.locks import document_lock

logger = get_task_logger(__name__)
slog = structlog.get_logger("pipeline")


# =========================================================================
# Celery app
# =========================================================================
celery_app = Celery(
    "ldip",
    broker=settings.CELERY_BROKER_URL,
    backend=settings.CELERY_RESULT_BACKEND,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_default_retry_delay=10,
    task_default_max_retries=3,
    broker_connection_retry_on_startup=True,
    task_routes={
        "app.workers.tasks.validate_document": {"queue": "default"},
        "app.workers.tasks.ocr_document": {"queue": "ocr"},
        "app.workers.tasks.chunk_document": {"queue": "default"},
        "app.workers.tasks.embed_document": {"queue": "embed"},
        "app.workers.tasks.index_document": {"queue": "default"},
    },
)


# =========================================================================
# Public entrypoint
# =========================================================================
def launch_pipeline(document_id: uuid.UUID | str) -> str:
    """Start the full ingestion chain for a document, return the chain id."""
    sig = chain(
        validate_document.s(str(document_id)),
        ocr_document.s(),
        chunk_document.s(),
        embed_document.s(),
        index_document.s(),
    )
    res = sig.apply_async()
    return res.id


# =========================================================================
# Helpers (all run inside a Celery worker, sync DB access)
# =========================================================================
def _emit(
    document_id: str,
    *,
    stage: str,
    progress_pct: int,
    message: str,
    extra: Optional[dict] = None,
) -> None:
    event = build_event(
        stage=stage, progress_pct=progress_pct, message=message, extra=extra
    )
    publish_sync(document_id, event)
    slog.info(
        "pipeline_event",
        document_id=document_id,
        stage=stage,
        progress=progress_pct,
        message=message,
    )


def _set_status(
    session,
    document_id: str,
    *,
    status: Optional[DocStatus] = None,
    error_message: Optional[str] = None,
    page_count: Optional[int] = None,
    language: Optional[str] = None,
    doc_type: Optional[DocType] = None,
    extra: Optional[dict] = None,
) -> None:
    doc = session.get(Document, uuid.UUID(document_id))
    if doc is None:
        return
    if status is not None:
        doc.status = status
    if error_message is not None:
        doc.error_message = error_message
    if page_count is not None:
        doc.page_count = page_count
    if language is not None:
        doc.language = language
    if doc_type is not None:
        doc.doc_type = doc_type
    if extra:
        doc.extra = {**(doc.extra or {}), **extra}
    session.add(doc)


def _update_job(
    session,
    document_id: str,
    *,
    state: Optional[JobState] = None,
    progress_pct: Optional[int] = None,
    stage: Optional[str] = None,
    message: Optional[str] = None,
    celery_task_id: Optional[str] = None,
    started: bool = False,
    finished: bool = False,
) -> Optional[IngestJob]:
    job = (
        session.execute(
            select(IngestJob)
            .where(IngestJob.document_id == uuid.UUID(document_id))
            .order_by(IngestJob.created_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if job is None:
        job = IngestJob(document_id=uuid.UUID(document_id), state=JobState.queued)
        session.add(job)
        session.flush()
    if state is not None:
        job.state = state
    if progress_pct is not None:
        job.progress_pct = progress_pct
    if stage is not None:
        job.stage = stage
    if message is not None:
        job.message = message
    if celery_task_id is not None:
        job.celery_task_id = celery_task_id
    now = datetime.now(timezone.utc)
    if started and job.started_at is None:
        job.started_at = now
    if finished:
        job.finished_at = now
    return job


def _fail(document_id: str, stage: str, exc: BaseException) -> None:
    metrics.PIPELINE_FAILURES.labels(stage=stage).inc()
    msg = f"{type(exc).__name__}: {exc}"
    with SyncSessionLocal() as session:
        _set_status(session, document_id, status=DocStatus.failed, error_message=msg)
        _update_job(
            session,
            document_id,
            state=JobState.failed,
            stage=stage,
            message=msg,
            finished=True,
        )
        session.commit()
    _emit(document_id, stage="failed", progress_pct=0, message=msg, extra={"failed_stage": stage})


# =========================================================================
# Stage 1: validate
# =========================================================================
@celery_app.task(
    bind=True,
    name="app.workers.tasks.validate_document",
    autoretry_for=(IOError, OSError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=60,
    retry_jitter=True,
    max_retries=3,
)
def validate_document(self, document_id: str) -> str:
    stage = "validate"
    try:
        with sync_engine.connect() as conn:
            with document_lock(conn, document_id) as acquired:
                if not acquired:
                    raise RuntimeError(
                        f"document {document_id} is locked by another worker"
                    )
                with SyncSessionLocal() as session:
                    doc = session.get(Document, uuid.UUID(document_id))
                    if doc is None:
                        raise FileNotFoundError(
                            f"document {document_id} not found in DB"
                        )
                    # Idempotent skip: terminal state.
                    if doc.status == DocStatus.ready:
                        _emit(
                            document_id,
                            stage=stage,
                            progress_pct=10,
                            message="already ready, skipping",
                        )
                        return document_id
                    if not os.path.exists(doc.stored_path):
                        raise FileNotFoundError(
                            f"stored file missing: {doc.stored_path}"
                        )
                    size = os.path.getsize(doc.stored_path)
                    if size == 0:
                        raise ValueError("empty file on disk")
                    if size != doc.size_bytes:
                        # Self-heal the row; not a fatal error.
                        doc.size_bytes = size
                    _set_status(session, document_id, status=DocStatus.validating)
                    _update_job(
                        session,
                        document_id,
                        state=JobState.running,
                        progress_pct=5,
                        stage=stage,
                        message="validated",
                        celery_task_id=self.request.id,
                        started=True,
                    )
                    session.commit()
        _emit(document_id, stage=stage, progress_pct=10, message="file validated")
        return document_id
    except Exception as exc:  # noqa: BLE001
        _fail(document_id, stage, exc)
        raise


# =========================================================================
# Stage 2: OCR
# =========================================================================
@celery_app.task(
    bind=True,
    name="app.workers.tasks.ocr_document",
    autoretry_for=(IOError, OSError, ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_backoff_max=120,
    retry_jitter=True,
    max_retries=2,
)
def ocr_document(self, document_id: str) -> str:
    from app.ai.classifier import classify_document_with_confidence
    from app.ai.ocr_engine import OCREngine

    stage = "ocr"
    started = time.monotonic()
    try:
        with SyncSessionLocal() as session:
            doc = session.get(Document, uuid.UUID(document_id))
            if doc is None:
                raise FileNotFoundError(f"document {document_id} not found")
            if doc.status == DocStatus.ready:
                _emit(document_id, stage=stage, progress_pct=35, message="already ready")
                return document_id

            stored_path = doc.stored_path
            mime = doc.mime_type
            owner_id = doc.owner_user_id

            _set_status(session, document_id, status=DocStatus.ocr)
            _update_job(
                session,
                document_id,
                stage=stage,
                progress_pct=15,
                message="starting OCR",
                celery_task_id=self.request.id,
            )
            session.commit()

        _emit(document_id, stage=stage, progress_pct=15, message="starting OCR")

        engine = OCREngine()
        # Run OCR synchronously (we are inside a Celery worker thread).
        # The async OCREngine.process is wrapped in to_thread — we just call sync paths.
        ext = OCREngine._classify(mime, stored_path)  # noqa: SLF001
        if ext == "pdf":
            ocr_result = engine._sync_process_pdf(stored_path)  # noqa: SLF001
        elif ext == "docx":
            ocr_result = engine._sync_process_docx(stored_path)  # noqa: SLF001
        elif ext in {"doc", "txt"}:
            ocr_result = engine._sync_process_txt(stored_path, ext)  # noqa: SLF001
        elif ext in {"png", "jpg", "jpeg", "tiff", "tif", "bmp"}:
            ocr_result = engine._sync_process_image(stored_path)  # noqa: SLF001
        else:
            raise ValueError(f"unsupported OCR ext: {ext}")
        ocr_result.language = OCREngine._detect_language(ocr_result.full_text)  # noqa: SLF001

        # Persist OCR text to disk under /data/uploads/{owner}/{doc}/ocr.txt.
        out_dir = os.path.dirname(stored_path)
        os.makedirs(out_dir, exist_ok=True)
        ocr_text_path = os.path.join(out_dir, "ocr.txt")
        with open(ocr_text_path + ".tmp", "w", encoding="utf-8") as fh:
            fh.write(ocr_result.full_text or "")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(ocr_text_path + ".tmp", ocr_text_path)

        # Persist a JSON sidecar of page spans so chunker can map char→page.
        spans_path = os.path.join(out_dir, "ocr_spans.json")
        import json as _json

        with open(spans_path + ".tmp", "w", encoding="utf-8") as fh:
            _json.dump(
                {
                    "page_spans": ocr_result.page_spans,
                    "metadata": ocr_result.metadata,
                    "language": ocr_result.language,
                },
                fh,
            )
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(spans_path + ".tmp", spans_path)

        classification = classify_document_with_confidence(ocr_result.full_text)
        classification_is_confident = (
            classification.confidence >= settings.DOCUMENT_CLASSIFICATION_MIN_CONFIDENCE
        )
        doc_type = (
            classification.doc_type
            if classification_is_confident
            else DocType.unknown
        )
        if not classification_is_confident:
            metrics.CLASSIFICATION_LOW_CONFIDENCE.inc()

        with SyncSessionLocal() as session:
            # Upsert the document_versions row (UNIQUE(document_id, kind)).
            existing = (
                session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == uuid.UUID(document_id),
                        DocumentVersion.kind == VersionKind.ocr_text,
                    )
                )
                .scalars()
                .first()
            )
            if existing:
                existing.storage_path = ocr_text_path
            else:
                session.add(
                    DocumentVersion(
                        document_id=uuid.UUID(document_id),
                        kind=VersionKind.ocr_text,
                        storage_path=ocr_text_path,
                    )
                )
            _set_status(
                session,
                document_id,
                page_count=ocr_result.total_pages,
                language=ocr_result.language,
                doc_type=doc_type,
                extra={
                    "classification": {
                        "detected_type": classification.doc_type.value,
                        "stored_type": doc_type.value,
                        "confidence": classification.confidence,
                        "reason": classification.reason,
                    }
                },
            )
            _update_job(
                session,
                document_id,
                stage=stage,
                progress_pct=35,
                message=(
                    f"OCR complete: {ocr_result.total_pages} pages, "
                    f"{len(ocr_result.full_text)} chars, lang={ocr_result.language}"
                ),
            )
            session.commit()

        metrics.OCR_DURATION.observe(time.monotonic() - started)
        _emit(
            document_id,
            stage=stage,
            progress_pct=35,
            message="OCR complete",
            extra={
                "pages": ocr_result.total_pages,
                "chars": len(ocr_result.full_text),
                "language": ocr_result.language,
                "doc_type": doc_type.value,
                "classification_confidence": classification.confidence,
                "classification_reason": classification.reason,
            },
        )
        return document_id
    except Exception as exc:  # noqa: BLE001
        _fail(document_id, stage, exc)
        raise


# =========================================================================
# Stage 3: chunk
# =========================================================================
@celery_app.task(
    bind=True,
    name="app.workers.tasks.chunk_document",
    autoretry_for=(IOError, OSError),
    retry_backoff=True,
    max_retries=2,
)
def chunk_document(self, document_id: str) -> str:  # noqa: ARG001
    from app.ai.chunker import DocumentChunker
    from app.ai.ocr_engine import OCRResult, PageResult

    stage = "chunk"
    started = time.monotonic()
    try:
        with SyncSessionLocal() as session:
            doc = session.get(Document, uuid.UUID(document_id))
            if doc is None:
                raise FileNotFoundError(f"document {document_id} not found")
            if doc.status == DocStatus.ready:
                _emit(document_id, stage=stage, progress_pct=55, message="already ready")
                return document_id
            ver = (
                session.execute(
                    select(DocumentVersion).where(
                        DocumentVersion.document_id == uuid.UUID(document_id),
                        DocumentVersion.kind == VersionKind.ocr_text,
                    )
                )
                .scalars()
                .first()
            )
            if ver is None or not os.path.exists(ver.storage_path):
                raise FileNotFoundError("OCR text version is missing on disk")
            ocr_text_path = ver.storage_path

            _set_status(session, document_id, status=DocStatus.chunking)
            _update_job(
                session,
                document_id,
                stage=stage,
                progress_pct=40,
                message="starting chunking",
            )
            session.commit()

        _emit(document_id, stage=stage, progress_pct=40, message="chunking")

        with open(ocr_text_path, "r", encoding="utf-8") as fh:
            full_text = fh.read()

        spans_path = os.path.join(os.path.dirname(ocr_text_path), "ocr_spans.json")
        page_spans: list = []
        if os.path.exists(spans_path):
            import json as _json

            with open(spans_path, "r", encoding="utf-8") as fh:
                payload = _json.load(fh)
            page_spans = [tuple(t) for t in payload.get("page_spans") or []]

        ocr_obj = OCRResult(
            full_text=full_text,
            pages=[PageResult(page_number=p[0], text="", method="cached") for p in page_spans]
            or [PageResult(page_number=1, text="", method="cached")],
            page_spans=page_spans or [(1, 0, len(full_text))],
        )

        chunker = DocumentChunker()
        chunks = chunker.chunk(ocr_obj, document_id)
        if not chunks:
            raise RuntimeError("no chunks produced from OCR text")

        # Idempotent insert: clear previous chunks for this document, then bulk insert.
        with SyncSessionLocal() as session:
            session.query(ChunkRow).filter(
                ChunkRow.document_id == uuid.UUID(document_id)
            ).delete(synchronize_session=False)
            session.bulk_save_objects(
                [
                    ChunkRow(
                        id=uuid.UUID(c.chunk_id),
                        document_id=uuid.UUID(document_id),
                        ordinal=c.ordinal,
                        text=c.text,
                        token_count=c.token_count,
                        char_start=c.char_start,
                        char_end=c.char_end,
                        page_start=c.page_start,
                        page_end=c.page_end,
                        section_path=c.section_path,
                        hash=c.hash,
                    )
                    for c in chunks
                ]
            )
            _update_job(
                session,
                document_id,
                stage=stage,
                progress_pct=55,
                message=f"produced {len(chunks)} chunks",
            )
            session.commit()

        metrics.CHUNK_DURATION.observe(time.monotonic() - started)
        metrics.CHUNKS_PRODUCED.inc(len(chunks))
        metrics.CHUNK_COUNT.observe(len(chunks))
        _emit(
            document_id,
            stage=stage,
            progress_pct=55,
            message=f"{len(chunks)} chunks",
            extra={"chunks": len(chunks)},
        )
        return document_id
    except Exception as exc:  # noqa: BLE001
        _fail(document_id, stage, exc)
        raise


# =========================================================================
# Stage 4: embed
# =========================================================================
@celery_app.task(
    bind=True,
    name="app.workers.tasks.embed_document",
    autoretry_for=(IOError, OSError, ConnectionError, TimeoutError),
    retry_backoff=True,
    max_retries=3,
)
def embed_document(self, document_id: str) -> str:  # noqa: ARG001
    from app.ai.embedder import LocalEmbedder
    from app.ai.vectorstore import FaissVectorStore, index_path

    stage = "embed"
    started = time.monotonic()
    try:
        with SyncSessionLocal() as session:
            doc = session.get(Document, uuid.UUID(document_id))
            if doc is None:
                raise FileNotFoundError(f"document {document_id} not found")
            if doc.status == DocStatus.ready:
                _emit(document_id, stage=stage, progress_pct=85, message="already ready")
                return document_id

            chunks = (
                session.execute(
                    select(ChunkRow)
                    .where(ChunkRow.document_id == uuid.UUID(document_id))
                    .order_by(ChunkRow.ordinal)
                )
                .scalars()
                .all()
            )
            if not chunks:
                raise RuntimeError("no chunks to embed")

            existing_meta = (
                session.execute(
                    select(EmbeddingsMeta).where(
                        EmbeddingsMeta.document_id == uuid.UUID(document_id),
                        EmbeddingsMeta.model_name == settings.EMBEDDING_MODEL_NAME,
                    )
                )
                .scalars()
                .first()
            )

            # Idempotency: if an embeddings_meta row exists with vector_count
            # equal to current chunk count, skip re-embedding.
            if existing_meta and existing_meta.vector_count == len(chunks):
                _emit(
                    document_id,
                    stage=stage,
                    progress_pct=85,
                    message="embeddings already up-to-date",
                )
                _set_status(session, document_id, status=DocStatus.indexing)
                _update_job(
                    session,
                    document_id,
                    stage=stage,
                    progress_pct=85,
                    message="skipped (idempotent)",
                )
                session.commit()
                return document_id

            chunk_payload = [(str(c.id), c.text) for c in chunks]
            _set_status(session, document_id, status=DocStatus.embedding)
            _update_job(
                session,
                document_id,
                stage=stage,
                progress_pct=60,
                message=f"embedding {len(chunk_payload)} chunks",
            )
            session.commit()

        _emit(
            document_id,
            stage=stage,
            progress_pct=60,
            message=f"embedding {len(chunk_payload)} chunks",
        )

        embedder = LocalEmbedder()
        store = FaissVectorStore()
        # Reset any pre-existing index to keep things deterministic on re-runs.
        store.delete_index(document_id)
        store.create_index(document_id, expected_size=len(chunk_payload))

        BATCH = 64
        total = len(chunk_payload)
        for batch_start in range(0, total, BATCH):
            batch = chunk_payload[batch_start : batch_start + BATCH]
            ids = [b[0] for b in batch]
            texts = [b[1] for b in batch]
            vectors = embedder.embed_documents(texts)
            store.add(document_id, vectors, ids)
            done = batch_start + len(batch)
            pct = 60 + int(20 * (done / total))
            _emit(
                document_id,
                stage=stage,
                progress_pct=pct,
                message=f"{done}/{total} embedded",
            )

        with SyncSessionLocal() as session:
            existing_meta = (
                session.execute(
                    select(EmbeddingsMeta).where(
                        EmbeddingsMeta.document_id == uuid.UUID(document_id),
                        EmbeddingsMeta.model_name == settings.EMBEDDING_MODEL_NAME,
                    )
                )
                .scalars()
                .first()
            )
            if existing_meta:
                existing_meta.vector_count = total
                existing_meta.faiss_index_path = index_path(document_id)
                existing_meta.dim = settings.EMBEDDING_DIM
            else:
                session.add(
                    EmbeddingsMeta(
                        document_id=uuid.UUID(document_id),
                        model_name=settings.EMBEDDING_MODEL_NAME,
                        dim=settings.EMBEDDING_DIM,
                        faiss_index_path=index_path(document_id),
                        vector_count=total,
                    )
                )
            _update_job(
                session,
                document_id,
                stage=stage,
                progress_pct=80,
                message=f"embedded {total} vectors",
            )
            session.commit()

        metrics.EMBEDDING_DURATION.observe(time.monotonic() - started)
        _emit(
            document_id,
            stage=stage,
            progress_pct=80,
            message="embedding complete",
            extra={"vectors": total},
        )
        return document_id
    except Exception as exc:  # noqa: BLE001
        _fail(document_id, stage, exc)
        raise


# =========================================================================
# Stage 5: index (finalisation)
# =========================================================================
@celery_app.task(
    bind=True,
    name="app.workers.tasks.index_document",
    autoretry_for=(IOError, OSError),
    retry_backoff=True,
    max_retries=2,
)
def index_document(self, document_id: str) -> str:  # noqa: ARG001
    from app.ai.vectorstore import FaissVectorStore

    stage = "index"
    started = time.monotonic()
    try:
        with SyncSessionLocal() as session:
            doc = session.get(Document, uuid.UUID(document_id))
            if doc is None:
                raise FileNotFoundError(f"document {document_id} not found")

            _set_status(session, document_id, status=DocStatus.indexing)
            _update_job(
                session,
                document_id,
                stage=stage,
                progress_pct=90,
                message="finalising index",
            )
            session.commit()

        _emit(document_id, stage=stage, progress_pct=90, message="finalising index")

        info = FaissVectorStore().get_info(document_id)
        if not info.get("exists"):
            raise RuntimeError("FAISS index missing at finalisation")

        with SyncSessionLocal() as session:
            _set_status(session, document_id, status=DocStatus.ready, error_message=None)
            _update_job(
                session,
                document_id,
                state=JobState.succeeded,
                stage=stage,
                progress_pct=100,
                message="ready",
                finished=True,
            )
            session.commit()

        metrics.INDEX_DURATION.observe(time.monotonic() - started)
        metrics.DOCUMENTS_INDEXED.inc()
        _emit(
            document_id,
            stage="ready",
            progress_pct=100,
            message="document is ready",
            extra={
                "vectors": info.get("vector_count"),
                "index_type": info.get("type"),
            },
        )
        return document_id
    except Exception as exc:  # noqa: BLE001
        _fail(document_id, stage, exc)
        raise
