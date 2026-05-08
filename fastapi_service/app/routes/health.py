"""Health endpoints for FastAPI service."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter
from sqlalchemy import text

from app.config import settings

router = APIRouter(tags=["Health"])


async def _check_postgres() -> str:
    try:
        from app.database import engine

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "connected"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


async def _check_redis() -> str:
    try:
        import redis.asyncio as aredis

        client = aredis.from_url(settings.REDIS_URL)
        try:
            await client.ping()
            return "connected"
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


def _check_gemini() -> str:
    if not settings.GEMINI_API_KEY:
        return "missing_api_key"
    try:
        import google.generativeai as genai

        genai.configure(api_key=settings.GEMINI_API_KEY)
        return "configured"
    except Exception as exc:  # noqa: BLE001
        return f"error: {exc}"


@router.get("/health")
@router.get("/health/")
async def health() -> dict:
    deps = {
        "postgres": await _check_postgres(),
        "redis": await _check_redis(),
        "gemini": _check_gemini(),
    }
    overall = (
        "healthy"
        if deps["postgres"] == "connected" and deps["redis"] == "connected"
        else "degraded"
    )
    return {
        "status": overall,
        "service": "fastapi",
        "version": "1.0.0",
        "dependencies": deps,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
