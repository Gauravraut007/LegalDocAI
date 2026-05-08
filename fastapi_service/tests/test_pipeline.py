"""Integration test: TXT upload → pipeline → ready.

Uses Celery in EAGER mode so tasks run synchronously inside the test process.
Requires a local Postgres + Redis for the FAISS / DB writes; if either is
unavailable the test is skipped automatically.
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid
from io import BytesIO

import pytest

pytest.importorskip("httpx")


def _redis_alive() -> bool:
    try:
        import redis

        r = redis.from_url(os.environ.get("REDIS_URL", "redis://localhost:6379/15"))
        r.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


def _postgres_alive() -> bool:
    try:
        from sqlalchemy import create_engine, text

        url = os.environ.get(
            "SYNC_DATABASE_URL",
            "postgresql+psycopg2://postgres:postgres@localhost:5432/fastapi_db",
        )
        e = create_engine(url, pool_pre_ping=True)
        with e.connect() as c:
            c.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not (_redis_alive() and _postgres_alive()),
    reason="requires running Redis + Postgres",
)


@pytest.mark.asyncio
async def test_txt_upload_pipeline_runs_to_ready(tmp_path) -> None:
    from httpx import ASGITransport, AsyncClient

    from app.main import app
    from app.workers.tasks import celery_app

    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True

    payload = b"This Agreement is made between Acme and Foo. Section 1. Confidentiality applies."
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        owner = str(uuid.uuid4())
        files = {"file": ("test.txt", BytesIO(payload), "text/plain")}
        data = {"owner_user_id": owner}
        resp = await client.post("/api/v1/documents/upload", files=files, data=data)
        assert resp.status_code == 202, resp.text
        body = resp.json()
        doc_id = body["document_id"]

        # In EAGER mode the chain has already run by the time upload returns,
        # but we poll to be defensive.
        for _ in range(20):
            r = await client.get(f"/api/v1/documents/{doc_id}")
            assert r.status_code == 200
            if r.json()["status"] in {"ready", "failed"}:
                break
            await asyncio.sleep(0.2)
        detail = r.json()
        assert detail["status"] == "ready", detail

        chunks = await client.get(f"/api/v1/documents/{doc_id}/chunks")
        assert chunks.status_code == 200
        assert chunks.json()["total"] >= 1

        text_resp = await client.get(f"/api/v1/documents/{doc_id}/text")
        assert text_resp.status_code == 200
        assert "Agreement" in text_resp.text
