"""WebSocket chat consumer streams tokens (FastAPI SSE mocked)."""
from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from channels.db import database_sync_to_async
from channels.testing import WebsocketCommunicator

from apps.chat.models import ChatSession
from apps.users.serializers import LDIPTokenObtainPairSerializer
from core.asgi import application


@database_sync_to_async
def _token_for(user):
    return str(LDIPTokenObtainPairSerializer.get_token(user).access_token)


@pytest.fixture
def chat_session(db, user, workspace):
    return ChatSession.objects.create(
        created_by=user, workspace=workspace,
        title="t", document_ids=[str(uuid.uuid4())],
    )


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_chat_ws_streams_tokens(user, chat_session):
    async def fake_stream(self, session_id, content):  # noqa: ARG001
        yield {"event": "token", "data": {"text": "Hello"}}
        yield {"event": "token", "data": {"text": " world"}}
        yield {"event": "sources", "data": {"sources": []}}
        yield {"event": "done", "data": {"finish_reason": "stop", "usage": {}}}

    token = await _token_for(user)
    comm = WebsocketCommunicator(
        application, f"/ws/chat/{chat_session.id}/?token={token}",
    )
    connected, _ = await comm.connect()
    assert connected

    ready = await comm.receive_json_from()
    assert ready["type"] == "ready"

    with patch(
        "apps.documents.services.fastapi_client.FastAPIClient.stream_chat_message",
        new=fake_stream,
    ):
        await comm.send_json_to({"type": "user_message", "content": "hi"})

        types_seen = []
        for _ in range(8):
            try:
                msg = await comm.receive_json_from(timeout=2)
            except Exception:  # noqa: BLE001
                break
            types_seen.append(msg["type"])
            if msg["type"] == "done":
                break
    await comm.disconnect()
    assert "user_message_ack" in types_seen
    assert "token" in types_seen
    assert "done" in types_seen


@pytest.mark.django_db(transaction=True)
@pytest.mark.asyncio
async def test_chat_ws_rejects_no_token(chat_session):
    comm = WebsocketCommunicator(application, f"/ws/chat/{chat_session.id}/")
    connected, code = await comm.connect()
    assert not connected
