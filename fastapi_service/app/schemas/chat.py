"""Chat / RAG schemas (Phase 3)."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class Source(BaseModel):
    chunk_id: uuid.UUID
    document_id: uuid.UUID
    filename: Optional[str] = None
    section_path: Optional[str] = None
    pages: Optional[List[int]] = None
    score: float = 0.0
    snippet: str = ""


# --------------------------------------------------------------- Sessions ---
class SessionCreate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=500)
    document_ids: List[uuid.UUID] = Field(min_length=1)
    workspace_id: Optional[uuid.UUID] = None


class SessionUpdate(BaseModel):
    title: Optional[str] = Field(default=None, max_length=500)


class SessionSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    user_id: uuid.UUID
    workspace_id: Optional[uuid.UUID] = None
    title: str
    document_ids: List[uuid.UUID] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


# --------------------------------------------------------------- Messages ---
class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    role: Literal["user", "assistant", "system"]
    content: str
    sources: List[Source] = Field(default_factory=list)
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    model_name: Optional[str] = None
    created_at: datetime


class SessionDetail(SessionSummary):
    messages: List[MessageOut] = Field(default_factory=list)


class SessionList(BaseModel):
    items: List[SessionSummary]
    total: int
    page: int
    page_size: int


# ----------------------------------------------------- Send/preview ---------
class MessageSend(BaseModel):
    content: str = Field(min_length=1, max_length=8000)
    doc_type_hint: Optional[str] = None
    system_prompt_override: Optional[str] = Field(default=None, max_length=8000)


class PreviewRequest(BaseModel):
    query: str = Field(min_length=1, max_length=8000)
    document_ids: List[uuid.UUID] = Field(min_length=1)
    chat_history: List[dict] = Field(default_factory=list)
    doc_type_hint: Optional[str] = None
    system_prompt_override: Optional[str] = Field(default=None, max_length=8000)
