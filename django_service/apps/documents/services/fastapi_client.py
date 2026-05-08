"""Async client used by the documents/chat gateways to call FastAPI.

The client is intentionally thin: it forwards the caller's bearer token and
maps upstream errors into ``FastAPIClientError``. Streaming methods are async
generators that yield raw bytes (SSE event lines).
"""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Mapping

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)


class FastAPIClientError(Exception):
    def __init__(self, status_code: int, detail: Any = None):
        super().__init__(f"FastAPI error {status_code}: {detail!r}")
        self.status_code = status_code
        self.detail = detail


def _base_url() -> str:
    return getattr(settings, "FASTAPI_BASE_URL", "http://fastapi:8001").rstrip("/")


def _timeout() -> httpx.Timeout:
    secs = getattr(settings, "FASTAPI_TIMEOUT_SECONDS", 120)
    return httpx.Timeout(connect=10.0, read=secs, write=secs, pool=10.0)


def _auth_headers(token: str) -> dict[str, str]:
    if not token:
        raise FastAPIClientError(401, "missing JWT for upstream call")
    return {"Authorization": f"Bearer {token}"}


async def _raise_for_status(resp: httpx.Response) -> None:
    if resp.status_code >= 400:
        try:
            detail = resp.json()
        except ValueError:
            detail = resp.text
        raise FastAPIClientError(resp.status_code, detail)


class FastAPIClient:
    """Async HTTP client for the FastAPI service."""

    def __init__(self, token: str):
        self._token = token
        self._client = httpx.AsyncClient(
            base_url=_base_url(),
            timeout=_timeout(),
            headers=_auth_headers(token),
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self._client.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    # ---------------- generic with retry on 5xx ----------------
    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: Any = None,
        data: Any = None,
        files: Any = None,
        params: Mapping[str, Any] | None = None,
        attempts: int = 3,
    ) -> httpx.Response:
        last_exc: Exception | None = None
        for i in range(attempts):
            try:
                resp = await self._client.request(
                    method, url,
                    json=json_body, data=data, files=files, params=params,
                )
            except httpx.HTTPError as exc:
                last_exc = exc
                await asyncio.sleep(0.25 * (2 ** i))
                continue
            if 500 <= resp.status_code < 600 and i < attempts - 1:
                await asyncio.sleep(0.25 * (2 ** i))
                continue
            return resp
        raise FastAPIClientError(503, f"upstream unavailable: {last_exc}")

    # =================== Documents ===================
    async def upload(
        self,
        *,
        filename: str,
        content_type: str,
        file_bytes: bytes,
        workspace_id: str | None = None,
    ) -> dict:
        files = {"file": (filename, file_bytes, content_type or "application/octet-stream")}
        data: dict[str, Any] = {}
        if workspace_id:
            data["workspace_id"] = str(workspace_id)
        resp = await self._request(
            "POST", "/api/v1/documents/upload",
            files=files, data=data, attempts=1,
        )
        await _raise_for_status(resp)
        return resp.json()

    async def get(self, doc_id: str) -> dict:
        resp = await self._request("GET", f"/api/v1/documents/{doc_id}")
        await _raise_for_status(resp)
        return resp.json()

    async def list(self, **filters) -> dict:
        params = {k: v for k, v in filters.items() if v is not None}
        resp = await self._request("GET", "/api/v1/documents/", params=params)
        await _raise_for_status(resp)
        return resp.json()

    async def delete(self, doc_id: str) -> None:
        resp = await self._request("DELETE", f"/api/v1/documents/{doc_id}", attempts=1)
        if resp.status_code not in (204, 404):
            await _raise_for_status(resp)

    async def stream_events(self, doc_id: str) -> AsyncIterator[dict]:
        """Async generator yielding parsed SSE frames as dicts.

        Each yielded item: ``{"event": str, "data": dict | str}``.
        """
        url = f"/api/v1/documents/{doc_id}/events"
        async with self._client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                detail = await resp.aread()
                raise FastAPIClientError(resp.status_code, detail.decode("utf-8", "ignore"))
            event = "message"
            data_lines: list[str] = []
            async for line in resp.aiter_lines():
                if line == "":
                    if data_lines:
                        raw = "\n".join(data_lines)
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            payload = raw
                        yield {"event": event, "data": payload}
                    event = "message"
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())

    # =================== Chat ===================
    async def create_chat_session(self, payload: dict) -> dict:
        resp = await self._request("POST", "/api/v1/chat/sessions", json_body=payload)
        await _raise_for_status(resp)
        return resp.json()

    async def get_chat_session(self, session_id: str) -> dict:
        resp = await self._request("GET", f"/api/v1/chat/sessions/{session_id}")
        await _raise_for_status(resp)
        return resp.json()

    async def list_chat_sessions(self, **filters) -> dict:
        params = {k: v for k, v in filters.items() if v is not None}
        resp = await self._request("GET", "/api/v1/chat/sessions", params=params)
        await _raise_for_status(resp)
        return resp.json()

    async def delete_chat_session(self, session_id: str) -> None:
        resp = await self._request(
            "DELETE", f"/api/v1/chat/sessions/{session_id}", attempts=1
        )
        if resp.status_code not in (204, 404):
            await _raise_for_status(resp)

    async def stream_chat_message(self, session_id: str, content: str) -> AsyncIterator[dict]:
        url = f"/api/v1/chat/sessions/{session_id}/messages"
        async with self._client.stream(
            "POST", url, json={"content": content},
        ) as resp:
            if resp.status_code >= 400:
                detail = await resp.aread()
                raise FastAPIClientError(resp.status_code, detail.decode("utf-8", "ignore"))
            event = "message"
            data_lines: list[str] = []
            async for line in resp.aiter_lines():
                if line == "":
                    if data_lines:
                        raw = "\n".join(data_lines)
                        try:
                            payload = json.loads(raw)
                        except json.JSONDecodeError:
                            payload = raw
                        yield {"event": event, "data": payload}
                    event = "message"
                    data_lines = []
                    continue
                if line.startswith(":"):
                    continue
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:"):
                    data_lines.append(line[5:].strip())
