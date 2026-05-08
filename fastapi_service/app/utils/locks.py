"""Postgres advisory locks scoped per document.

Used to guarantee that two Celery workers cannot process the same document
concurrently. Locks are session-scoped (auto-released when the connection closes).
"""
from __future__ import annotations

import contextlib
import logging
import uuid
from typing import Iterator

from sqlalchemy import text
from sqlalchemy.engine import Connection

logger = logging.getLogger(__name__)


def _lock_key(document_id: uuid.UUID | str) -> int:
    """Stable signed-bigint key derived from a UUID."""
    if isinstance(document_id, str):
        document_id = uuid.UUID(document_id)
    # 64-bit int from the UUID; clamp to signed bigint range used by pg_advisory_lock.
    raw = document_id.int & ((1 << 63) - 1)
    return int(raw)


def try_acquire(conn: Connection, document_id: uuid.UUID | str) -> bool:
    """Non-blocking attempt to acquire the per-document advisory lock."""
    key = _lock_key(document_id)
    res = conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}).scalar()
    return bool(res)


def release(conn: Connection, document_id: uuid.UUID | str) -> bool:
    key = _lock_key(document_id)
    res = conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key}).scalar()
    return bool(res)


@contextlib.contextmanager
def document_lock(conn: Connection, document_id: uuid.UUID | str) -> Iterator[bool]:
    """Context manager: yields True if the lock was acquired."""
    acquired = try_acquire(conn, document_id)
    try:
        yield acquired
    finally:
        if acquired:
            try:
                release(conn, document_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("advisory unlock failed: %s", exc)
