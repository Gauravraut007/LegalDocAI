"""Redis pub/sub helpers for ingestion progress events.

Channel format: ``ingest:{document_id}``
Payload (JSON): {stage, progress_pct, message, ts}.

The latest event is also stored under key ``ingest:{document_id}:latest`` with a
1-hour TTL, so SSE subscribers that connect mid-pipeline can replay current state.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from app.config import settings

logger = logging.getLogger(__name__)


def _channel(document_id: uuid.UUID | str) -> str:
    return f"ingest:{document_id}"


def _latest_key(document_id: uuid.UUID | str) -> str:
    return f"ingest:{document_id}:latest"


def build_event(
    *,
    stage: str,
    progress_pct: int,
    message: str,
    extra: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "stage": stage,
        "progress_pct": int(max(0, min(100, progress_pct))),
        "message": message,
        "ts": datetime.now(timezone.utc).isoformat(),
    }
    if extra:
        payload["extra"] = extra
    return payload


def publish_sync(document_id: uuid.UUID | str, event: dict[str, Any]) -> None:
    """Synchronous publish (used inside Celery tasks)."""
    try:
        import redis

        client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            payload = json.dumps(event, ensure_ascii=False)
            client.publish(_channel(document_id), payload)
            client.set(_latest_key(document_id), payload, ex=3600)
        finally:
            client.close()
    except Exception as exc:  # noqa: BLE001
        logger.debug("publish_sync failed for %s: %s", document_id, exc)


async def publish_async(document_id: uuid.UUID | str, event: dict[str, Any]) -> None:
    """Async publish (used inside FastAPI request handlers)."""
    try:
        import redis.asyncio as aredis

        client = aredis.from_url(settings.REDIS_URL, decode_responses=True)
        try:
            payload = json.dumps(event, ensure_ascii=False)
            await client.publish(_channel(document_id), payload)
            await client.set(_latest_key(document_id), payload, ex=3600)
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        logger.debug("publish_async failed for %s: %s", document_id, exc)
