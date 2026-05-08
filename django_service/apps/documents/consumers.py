"""Channels consumer that bridges FastAPI SSE doc events → WebSocket frames."""
from __future__ import annotations

import asyncio
import json
import logging

from channels.generic.websocket import AsyncJsonWebsocketConsumer

from apps.documents.models import Document
from apps.documents.services.fastapi_client import FastAPIClient, FastAPIClientError
from apps.workspaces.models import WorkspaceMember
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)


@database_sync_to_async
def _get_doc_and_check(user, doc_id):
    try:
        doc = Document.objects.get(pk=doc_id)
    except Document.DoesNotExist:
        return None, False
    if user.is_staff:
        return doc, True
    is_member = WorkspaceMember.objects.filter(
        workspace=doc.workspace, user=user
    ).exists()
    return doc, is_member


class DocumentEventsConsumer(AsyncJsonWebsocketConsumer):
    """``/ws/documents/{document_id}/events/``."""

    async def connect(self):
        self.document_id = self.scope["url_route"]["kwargs"]["document_id"]
        user = self.scope.get("user")
        token = self.scope.get("jwt_token")
        if not user or not user.is_authenticated or not token:
            await self.close(code=4401)
            return
        doc, allowed = await _get_doc_and_check(user, self.document_id)
        if doc is None:
            await self.close(code=4404)
            return
        if not allowed:
            await self.close(code=4403)
            return
        self._token = token
        await self.accept()
        self._task = asyncio.create_task(self._pump())

    async def disconnect(self, code):  # noqa: ARG002
        task = getattr(self, "_task", None)
        if task and not task.done():
            task.cancel()

    async def _pump(self):
        try:
            async with FastAPIClient(self._token) as client:
                async for frame in client.stream_events(self.document_id):
                    await self.send_json({
                        "type": frame.get("event") or "message",
                        "data": frame.get("data"),
                    })
                    if frame.get("event") == "end":
                        break
        except asyncio.CancelledError:
            raise
        except FastAPIClientError as exc:
            await self.send_json({
                "type": "error",
                "status": exc.status_code,
                "detail": exc.detail,
            })
        except Exception as exc:  # noqa: BLE001
            logger.exception("doc_events_bridge_error")
            await self.send_json({"type": "error", "detail": str(exc)})
        finally:
            await self.close()
