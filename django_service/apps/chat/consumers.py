"""WebSocket consumer for streaming RAG chat.

Client connects to ``ws://.../ws/chat/{session_id}/?token=<JWT>``.
The consumer:
    * Authenticates via the JWT in the query string.
    * Joins the session-scoped channel group (multi-tab broadcast).
    * On each ``{type:"user_message", content}`` from the client, opens an
      SSE stream to FastAPI and forwards parsed events to the client and to
      the group as ``{type:"token", data}``, ending with
      ``{type:"done", sources, message_id}``.
    * Cancels the upstream task on client disconnect.
"""
from __future__ import annotations

import asyncio
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.chat.models import ChatSession
from apps.documents.services.fastapi_client import FastAPIClient, FastAPIClientError
from apps.workspaces.models import WorkspaceMember

logger = logging.getLogger(__name__)


@database_sync_to_async
def _resolve_session_for_user(user, session_id):
    try:
        sess = ChatSession.objects.get(pk=session_id)
    except ChatSession.DoesNotExist:
        return None, False
    if user.is_staff or sess.created_by_id == user.id:
        return sess, True
    if sess.workspace_id and WorkspaceMember.objects.filter(
        workspace_id=sess.workspace_id, user=user
    ).exists():
        return sess, True
    return sess, False


class ChatConsumer(AsyncJsonWebsocketConsumer):
    """``/ws/chat/{session_id}/?token=...``."""

    async def connect(self):
        self.session_id = self.scope["url_route"]["kwargs"]["session_id"]
        user = self.scope.get("user")
        token = self.scope.get("jwt_token")
        if not user or not user.is_authenticated or not token:
            await self.close(code=4401)
            return
        sess, allowed = await _resolve_session_for_user(user, self.session_id)
        if sess is None:
            await self.close(code=4404)
            return
        if not allowed:
            await self.close(code=4403)
            return
        self._token = token
        self._group = f"chat.{self.session_id}"
        self._stream_task: asyncio.Task | None = None
        await self.channel_layer.group_add(self._group, self.channel_name)
        await self.accept()
        await self.send_json({"type": "ready", "session_id": str(self.session_id)})

    async def disconnect(self, code):  # noqa: ARG002
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
        try:
            await self.channel_layer.group_discard(self._group, self.channel_name)
        except Exception:  # noqa: BLE001
            pass

    async def receive_json(self, content, **kwargs):
        msg_type = (content or {}).get("type")
        if msg_type == "ping":
            await self.send_json({"type": "pong"})
            return
        if msg_type != "user_message":
            await self.send_json({"type": "error", "detail": f"unknown type: {msg_type}"})
            return
        text = (content.get("content") or "").strip()
        if not text:
            await self.send_json({"type": "error", "detail": "content required"})
            return
        if self._stream_task and not self._stream_task.done():
            await self.send_json({"type": "error", "detail": "previous message still streaming"})
            return
        self._stream_task = asyncio.create_task(self._stream(text))

    async def _stream(self, text: str):
        await self._broadcast({"type": "user_message_ack", "content": text})
        try:
            async with FastAPIClient(self._token) as client:
                async for frame in client.stream_chat_message(self.session_id, text):
                    event = frame.get("event") or "message"
                    data = frame.get("data")
                    if event == "token":
                        await self._broadcast({"type": "token", "data": data})
                    elif event == "sources":
                        await self._broadcast({"type": "sources", "data": data})
                    elif event == "user_message":
                        await self._broadcast({"type": "user_message_persisted", "data": data})
                    elif event == "done":
                        await self._broadcast({"type": "done", "data": data})
                    elif event == "persisted":
                        await self._broadcast({"type": "persisted", "data": data})
                    elif event == "error":
                        await self._broadcast({"type": "error", "data": data})
                    else:
                        await self._broadcast({"type": event, "data": data})
        except asyncio.CancelledError:
            raise
        except FastAPIClientError as exc:
            await self._broadcast({
                "type": "error", "status": exc.status_code, "detail": exc.detail,
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat_bridge_error")
            await self._broadcast({"type": "error", "detail": str(exc)})

    async def _broadcast(self, payload: dict):
        await self.channel_layer.group_send(
            self._group, {"type": "chat.broadcast", "payload": payload}
        )

    async def chat_broadcast(self, event):
        await self.send_json(event["payload"])
