"""Chat sessions + messages + SSE streaming RAG."""
from __future__ import annotations

import asyncio
import time
import uuid
from typing import AsyncGenerator, List, Optional

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import Response, StreamingResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.rag import RAGEvent, RAGRequest, RAGService
from app.database import get_db
from app.models.chat import ChatMessage, ChatSession, MessageRole
from app.models.document import DocStatus, DocType, Document
from app.schemas.chat import (
    MessageOut,
    MessageSend,
    PreviewRequest,
    SessionCreate,
    SessionDetail,
    SessionList,
    SessionSummary,
    SessionUpdate,
    Source,
)
from app.security import AuthenticatedUser, get_current_user
from app.security.rate_limit import chat_rate_limit

logger = structlog.get_logger("chat")
router = APIRouter(
    prefix="/api/v1/chat",
    tags=["chat"],
    dependencies=[Depends(get_current_user)],
)

_rag_service = RAGService()


# =========================================================================
# Helpers
# =========================================================================
async def _user_can_use_documents(
    session: AsyncSession,
    user: AuthenticatedUser,
    document_ids: List[uuid.UUID],
) -> List[Document]:
    if not document_ids:
        raise HTTPException(status_code=400, detail="no document_ids provided")
    rows = (
        await session.execute(select(Document).where(Document.id.in_(document_ids)))
    ).scalars().all()
    found_ids = {d.id for d in rows}
    missing = [str(i) for i in document_ids if i not in found_ids]
    if missing:
        raise HTTPException(status_code=404, detail=f"documents not found: {missing}")
    for d in rows:
        if not user.can_access_document(d.owner_user_id, d.workspace_id):
            raise HTTPException(
                status_code=403,
                detail=f"document {d.id} is not accessible",
            )
    return rows


async def _get_session_for_user(
    session: AsyncSession,
    user: AuthenticatedUser,
    session_id: uuid.UUID,
) -> ChatSession:
    chat = await session.get(ChatSession, session_id)
    if chat is None:
        raise HTTPException(status_code=404, detail="session not found")
    if not user.is_staff and chat.user_id != user.user_id:
        raise HTTPException(status_code=403, detail="forbidden")
    return chat


def _resolve_doc_type_hint(value: Optional[str]) -> Optional[DocType]:
    if not value:
        return None
    try:
        return DocType(value.lower())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"invalid doc_type_hint: {value}") from exc


def _to_messages_payload(messages) -> List[MessageOut]:
    out: List[MessageOut] = []
    for m in messages:
        sources = [Source.model_validate(s) for s in (m.sources or [])]
        out.append(
            MessageOut(
                id=m.id,
                session_id=m.session_id,
                role=m.role.value if hasattr(m.role, "value") else str(m.role),
                content=m.content,
                sources=sources,
                tokens_in=m.tokens_in,
                tokens_out=m.tokens_out,
                latency_ms=m.latency_ms,
                model_name=m.model_name,
                created_at=m.created_at,
            )
        )
    return out


# =========================================================================
# Sessions CRUD
# =========================================================================
@router.post("/sessions", response_model=SessionSummary, status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: SessionCreate,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> SessionSummary:
    docs = await _user_can_use_documents(session, user, payload.document_ids)
    title = payload.title or (docs[0].original_filename if docs else "New chat")[:200]
    chat = ChatSession(
        user_id=user.user_id,
        workspace_id=payload.workspace_id,
        title=title,
        document_ids=[str(d.id) for d in docs],
    )
    session.add(chat)
    await session.commit()
    await session.refresh(chat)
    return SessionSummary.model_validate(chat, from_attributes=True)


@router.get("/sessions", response_model=SessionList)
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> SessionList:
    if user.is_staff:
        stmt = select(ChatSession)
    else:
        clauses = [ChatSession.user_id == user.user_id]
        if user.workspace_ids:
            clauses.append(ChatSession.workspace_id.in_(user.workspace_ids))
        stmt = select(ChatSession).where(or_(*clauses))
    total = (
        await session.execute(select(func.count()).select_from(stmt.subquery()))
    ).scalar_one()
    rows = (
        await session.execute(
            stmt.order_by(ChatSession.updated_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()
    return SessionList(
        items=[SessionSummary.model_validate(r, from_attributes=True) for r in rows],
        total=int(total),
        page=page,
        page_size=page_size,
    )


@router.get("/sessions/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> SessionDetail:
    chat = await _get_session_for_user(session, user, session_id)
    msgs = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id)
            .order_by(ChatMessage.created_at)
        )
    ).scalars().all()
    summary = SessionSummary.model_validate(chat, from_attributes=True)
    return SessionDetail(
        **summary.model_dump(),
        messages=_to_messages_payload(msgs),
    )


@router.patch("/sessions/{session_id}", response_model=SessionSummary)
async def update_session(
    session_id: uuid.UUID,
    payload: SessionUpdate,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> SessionSummary:
    chat = await _get_session_for_user(session, user, session_id)
    if payload.title is not None:
        chat.title = payload.title
    await session.commit()
    await session.refresh(chat)
    return SessionSummary.model_validate(chat, from_attributes=True)


@router.delete(
    "/sessions/{session_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_session(
    session_id: uuid.UUID,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(get_current_user),
) -> Response:
    chat = await _get_session_for_user(session, user, session_id)
    await session.delete(chat)
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# =========================================================================
# Stream a message
# =========================================================================
@router.post("/sessions/{session_id}/messages")
async def send_message(
    session_id: uuid.UUID,
    payload: MessageSend,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(chat_rate_limit()),
) -> StreamingResponse:
    chat = await _get_session_for_user(session, user, session_id)
    doc_ids = [uuid.UUID(d) for d in chat.document_ids]
    docs = await _user_can_use_documents(session, user, doc_ids)
    not_ready = [str(d.id) for d in docs if d.status != DocStatus.ready]
    if not_ready:
        raise HTTPException(
            status_code=409, detail=f"documents not ready: {not_ready}"
        )

    # Persist the user message immediately.
    user_msg = ChatMessage(
        session_id=session_id,
        role=MessageRole.user,
        content=payload.content,
    )
    session.add(user_msg)
    await session.commit()
    await session.refresh(user_msg)

    # Build chat history from prior messages (oldest → newest).
    prior = (
        await session.execute(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.id != user_msg.id)
            .order_by(ChatMessage.created_at)
        )
    ).scalars().all()
    history = [{"role": m.role.value, "content": m.content} for m in prior]

    rag_request = RAGRequest(
        query=payload.content,
        document_ids=doc_ids,
        chat_history=history,
        system_prompt_override=payload.system_prompt_override,
        doc_type_hint=_resolve_doc_type_hint(payload.doc_type_hint),
    )

    return StreamingResponse(
        _stream_response(
            request, session, chat, user_msg.id, rag_request, user.is_staff
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


async def _stream_response(
    request: Request,
    session: AsyncSession,
    chat: ChatSession,
    user_message_id: uuid.UUID,
    rag_request: RAGRequest,
    is_staff: bool,
) -> AsyncGenerator[bytes, None]:
    started = time.monotonic()
    full_text_parts: list[str] = []
    sources_payload: list[dict] = []
    usage: dict = {}
    finish_reason = "stop"
    last_heartbeat = time.monotonic()

    # Send initial event with the user message id so the UI can correlate.
    yield (
        f'event: user_message\ndata: {{"id": "{user_message_id}"}}\n\n'
    ).encode("utf-8")

    try:
        async for event in _rag_service.run_stream(
            rag_request, session, is_staff=is_staff
        ):
            if await request.is_disconnected():
                logger.info("client_disconnected", session_id=str(chat.id))
                break
            now = time.monotonic()
            if now - last_heartbeat > 15:
                yield b": heartbeat\n\n"
                last_heartbeat = now

            if event.type == "token":
                full_text_parts.append(event.data.get("text", ""))
            elif event.type == "sources":
                sources_payload = event.data.get("sources", [])
            elif event.type == "done":
                finish_reason = event.data.get("finish_reason") or "stop"
                usage = event.data.get("usage") or {}
                if not full_text_parts and event.data.get("text"):
                    full_text_parts.append(event.data["text"])
                if not sources_payload:
                    sources_payload = event.data.get("sources", [])

            yield event.to_sse()
    except Exception as exc:  # noqa: BLE001
        logger.exception("rag_stream_error", error=str(exc))
        yield RAGEvent("error", {"message": str(exc)}).to_sse()
        finish_reason = "error"

    # Persist the assistant message.
    assistant_text = "".join(full_text_parts).strip()
    if assistant_text or sources_payload:
        latency_ms = int((time.monotonic() - started) * 1000)
        msg = ChatMessage(
            session_id=chat.id,
            role=MessageRole.assistant,
            content=assistant_text or "(no response)",
            sources=sources_payload,
            tokens_in=int((usage or {}).get("input_tokens", 0)),
            tokens_out=int((usage or {}).get("output_tokens", 0)),
            latency_ms=latency_ms,
            model_name=_rag_service.llm.model_name,
        )
        session.add(msg)
        try:
            await session.commit()
            await session.refresh(msg)
            yield (
                f'event: persisted\ndata: {{"id": "{msg.id}",'
                f' "finish_reason": "{finish_reason}"}}\n\n'
            ).encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            await session.rollback()
            logger.exception("persist_assistant_failed", error=str(exc))


# =========================================================================
# Preview (one-shot, no persistence)
# =========================================================================
@router.post("/preview")
async def preview(
    payload: PreviewRequest,
    request: Request,
    session: AsyncSession = Depends(get_db),
    user: AuthenticatedUser = Depends(chat_rate_limit()),
) -> StreamingResponse:
    docs = await _user_can_use_documents(session, user, payload.document_ids)
    not_ready = [str(d.id) for d in docs if d.status != DocStatus.ready]
    if not_ready:
        raise HTTPException(
            status_code=409, detail=f"documents not ready: {not_ready}"
        )

    rag_request = RAGRequest(
        query=payload.query,
        document_ids=payload.document_ids,
        chat_history=payload.chat_history or [],
        system_prompt_override=payload.system_prompt_override,
        doc_type_hint=_resolve_doc_type_hint(payload.doc_type_hint),
    )

    async def gen() -> AsyncGenerator[bytes, None]:
        last_heartbeat = time.monotonic()
        try:
            async for event in _rag_service.run_stream(
                rag_request, session, is_staff=user.is_staff
            ):
                if await request.is_disconnected():
                    break
                now = time.monotonic()
                if now - last_heartbeat > 15:
                    yield b": heartbeat\n\n"
                    last_heartbeat = now
                yield event.to_sse()
        except Exception as exc:  # noqa: BLE001
            yield RAGEvent("error", {"message": str(exc)}).to_sse()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )
