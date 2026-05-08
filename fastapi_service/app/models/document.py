"""SQLAlchemy ORM models for the document ingestion pipeline (Phase 2).

Schema:
- documents             : top-level document metadata + lifecycle status.
- document_versions     : on-disk artefacts (original, ocr_text, normalized).
- chunks                : ordered, page-mapped text chunks fed to the embedder.
- embeddings_meta       : pointer to the on-disk FAISS index for a document.
- ingest_jobs           : Celery pipeline run-state for a document.
"""
from __future__ import annotations

import enum
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    BigInteger,
    DateTime,
    Enum as SAEnum,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


# =========================================================================
# Enums
# =========================================================================
class DocType(str, enum.Enum):
    contract = "contract"
    nda = "nda"
    filing = "filing"
    general = "general"
    unknown = "unknown"


class DocStatus(str, enum.Enum):
    uploaded = "uploaded"
    validating = "validating"
    ocr = "ocr"
    chunking = "chunking"
    embedding = "embedding"
    indexing = "indexing"
    ready = "ready"
    failed = "failed"


TERMINAL_STATES: frozenset[DocStatus] = frozenset({DocStatus.ready, DocStatus.failed})


class VersionKind(str, enum.Enum):
    original = "original"
    ocr_text = "ocr_text"
    normalized = "normalized"


class JobState(str, enum.Enum):
    queued = "queued"
    running = "running"
    succeeded = "succeeded"
    failed = "failed"
    cancelled = "cancelled"


# =========================================================================
# Models
# =========================================================================
class Document(Base):
    __tablename__ = "documents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    owner_user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    workspace_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    stored_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    mime_type: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    doc_type: Mapped[DocType] = mapped_column(
        SAEnum(DocType, name="doc_type_enum"),
        default=DocType.unknown,
        nullable=False,
    )
    status: Mapped[DocStatus] = mapped_column(
        SAEnum(DocStatus, name="doc_status_enum"),
        default=DocStatus.uploaded,
        nullable=False,
    )
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    language: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    versions: Mapped[list["DocumentVersion"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    chunks: Mapped[list["Chunk"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    embeddings: Mapped[list["EmbeddingsMeta"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )
    jobs: Mapped[list["IngestJob"]] = relationship(
        back_populates="document", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("owner_user_id", "sha256", name="uq_documents_owner_sha256"),
        Index("ix_documents_owner_user_id", "owner_user_id"),
        Index("ix_documents_workspace_id", "workspace_id"),
        Index("ix_documents_status", "status"),
        Index("ix_documents_doc_type", "doc_type"),
        Index("ix_documents_created_at", "created_at"),
    )


class DocumentVersion(Base):
    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[VersionKind] = mapped_column(
        SAEnum(VersionKind, name="version_kind_enum"), nullable=False
    )
    storage_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="versions")

    __table_args__ = (
        Index("ix_document_versions_document_id", "document_id"),
        UniqueConstraint("document_id", "kind", name="uq_document_versions_doc_kind"),
    )


class Chunk(Base):
    __tablename__ = "chunks"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    page_start: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    page_end: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    char_start: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    char_end: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    section_path: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="chunks")

    __table_args__ = (
        Index("ix_chunks_document_id", "document_id"),
        UniqueConstraint("document_id", "ordinal", name="uq_chunks_doc_ordinal"),
        UniqueConstraint("document_id", "hash", name="uq_chunks_doc_hash"),
    )


class EmbeddingsMeta(Base):
    __tablename__ = "embeddings_meta"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_name: Mapped[str] = mapped_column(String(255), nullable=False)
    dim: Mapped[int] = mapped_column(Integer, nullable=False)
    faiss_index_path: Mapped[str] = mapped_column(String(1000), nullable=False)
    vector_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="embeddings")

    __table_args__ = (
        Index("ix_embeddings_meta_document_id", "document_id"),
        UniqueConstraint(
            "document_id", "model_name", name="uq_embeddings_meta_doc_model"
        ),
    )


class IngestJob(Base):
    __tablename__ = "ingest_jobs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    celery_task_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    state: Mapped[JobState] = mapped_column(
        SAEnum(JobState, name="job_state_enum"),
        default=JobState.queued,
        nullable=False,
    )
    progress_pct: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stage: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    document: Mapped["Document"] = relationship(back_populates="jobs")

    __table_args__ = (
        Index("ix_ingest_jobs_document_id", "document_id"),
        Index("ix_ingest_jobs_state", "state"),
    )
