"""Pydantic v2 schemas for document endpoints (Phase 2)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional

from pydantic import BaseModel, ConfigDict, Field


# =========================================================================
# Upload
# =========================================================================
class DocumentUploadResponse(BaseModel):
    document_id: uuid.UUID
    ingest_job_id: uuid.UUID
    status: str
    deduplicated: bool = False
    message: str
    original_filename: str
    size_bytes: int
    sha256: str


# =========================================================================
# Document
# =========================================================================
class DocumentSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    owner_user_id: uuid.UUID
    workspace_id: Optional[uuid.UUID] = None
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    doc_type: str
    status: str
    page_count: Optional[int] = None
    language: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class IngestJobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    celery_task_id: Optional[str] = None
    state: str
    progress_pct: int = Field(ge=0, le=100)
    stage: Optional[str] = None
    message: Optional[str] = None
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    created_at: datetime


class DocumentDetail(DocumentSummary):
    error_message: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)
    stored_path: str
    latest_job: Optional[IngestJobOut] = None


class DocumentList(BaseModel):
    items: List[DocumentSummary]
    total: int
    page: int
    page_size: int


# =========================================================================
# Chunks
# =========================================================================
class ChunkOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    document_id: uuid.UUID
    ordinal: int
    text: str
    token_count: int
    char_start: int
    char_end: int
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    section_path: Optional[str] = None
    hash: str


class PaginatedChunks(BaseModel):
    items: List[ChunkOut]
    total: int
    page: int
    page_size: int


# =========================================================================
# SSE event payload (publish/subscribe contract)
# =========================================================================
class IngestEvent(BaseModel):
    stage: str
    progress_pct: int = Field(ge=0, le=100)
    message: str
    ts: datetime
    extra: Optional[dict[str, Any]] = None
