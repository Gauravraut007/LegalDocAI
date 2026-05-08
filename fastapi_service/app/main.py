"""FastAPI application entrypoint.

- Mounts /health, /metrics (Prometheus), CORS, request-id middleware,
  structlog JSON logging, RFC7807 problem+json error responses.
"""
from __future__ import annotations

import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from prometheus_fastapi_instrumentator import Instrumentator
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import settings
from app.database import close_db, init_db
from app.routes.health import router as health_router

logger = structlog.get_logger("app")


# --------------------------------------------------------------- LOGGING ---
def _configure_logging() -> None:
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# --------------------------------------------------------------- LIFESPAN ---
@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_logging()
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    os.makedirs(settings.PROCESSED_DIR, exist_ok=True)
    os.makedirs(settings.FAISS_INDEX_DIR, exist_ok=True)
    try:
        await init_db()
    except Exception as exc:  # noqa: BLE001
        logger.error("init_db_failed", error=str(exc))
    yield
    await close_db()


app = FastAPI(
    title="Legal Document Intelligence API",
    description="Document processing + RAG service (FAISS + sentence-transformers + Gemini).",
    version="1.0.0",
    lifespan=lifespan,
)

# --------------------------------------------------------------- METRICS ---
Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/health", "/metrics"],
).instrument(app).expose(app, endpoint="/metrics", include_in_schema=False)

# --------------------------------------------------------------- CORS ------
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID"],
)


# --------------------------------------------------- REQUEST-ID middleware --
@app.middleware("http")
async def request_id_middleware(request: Request, call_next):
    rid = request.headers.get("X-Request-ID") or uuid.uuid4().hex
    request.state.request_id = rid
    structlog.contextvars.bind_contextvars(request_id=rid, path=request.url.path)
    try:
        response = await call_next(request)
    finally:
        structlog.contextvars.clear_contextvars()
    response.headers["X-Request-ID"] = rid
    return response


# ----------------------------------------- RFC7807 problem+json helpers ----
def _problem(
    *,
    status_code: int,
    title: str,
    detail: str,
    request: Request,
    type_: str = "about:blank",
    extra: Any | None = None,
) -> JSONResponse:
    body = {
        "type": type_,
        "title": title,
        "status": status_code,
        "detail": detail,
        "instance": str(request.url),
        "request_id": getattr(request.state, "request_id", None),
    }
    if extra is not None:
        body["errors"] = extra
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type="application/problem+json",
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    return _problem(
        status_code=422,
        title="Validation Error",
        detail="Invalid request payload.",
        request=request,
        type_="https://httpstatuses.com/422",
        extra=exc.errors(),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    return _problem(
        status_code=exc.status_code,
        title=exc.__class__.__name__,
        detail=str(exc.detail),
        request=request,
        type_=f"https://httpstatuses.com/{exc.status_code}",
    )


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("unhandled_exception", error=str(exc))
    return _problem(
        status_code=500,
        title="Internal Server Error",
        detail=str(exc),
        request=request,
        type_="https://httpstatuses.com/500",
    )


# --------------------------------------------------------------- ROUTES ----
app.include_router(health_router)

# Documents pipeline (Phase 2).
try:
    from app.routes.documents import router as documents_router

    app.include_router(documents_router)
except Exception as exc:  # noqa: BLE001
    logger.warning("documents_router_skipped", error=str(exc))

# Chat / RAG (Phase 3).
try:
    from app.routes.chat import router as chat_router

    app.include_router(chat_router)
except Exception as exc:  # noqa: BLE001
    logger.warning("chat_router_skipped", error=str(exc))

# Sentry init (optional, no-op without DSN).
try:  # pragma: no cover
    if settings.SENTRY_DSN:
        import sentry_sdk

        sentry_sdk.init(dsn=settings.SENTRY_DSN, traces_sample_rate=0.05)
except Exception as exc:  # noqa: BLE001
    logger.debug("sentry_init_skipped", error=str(exc))


@app.get("/", tags=["Root"], include_in_schema=False)
async def root():
    return {
        "service": "Legal Document Intelligence API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health",
        "metrics": "/metrics",
    }
