"""FastAPI application configuration.

All values are loaded from environment variables (12-factor). Defaults are
container-friendly (paths under /data, hosts pointing at compose service names).
"""
from __future__ import annotations

from functools import lru_cache
from typing import List, Optional

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ------------------------------------------------------------------ env
    ENVIRONMENT: str = "development"
    LOG_LEVEL: str = "INFO"

    # ------------------------------------------------------------- database
    # Async URL is the canonical one. Sync URL is used by Alembic / Celery.
    DATABASE_URL: str = (
        "postgresql+asyncpg://postgres:postgres@postgres:5432/fastapi_db"
    )
    SYNC_DATABASE_URL: str = (
        "postgresql+psycopg2://postgres:postgres@postgres:5432/fastapi_db"
    )

    # ---------------------------------------------------------------- redis
    REDIS_URL: str = "redis://redis:6379/0"
    CELERY_BROKER_URL: str = "redis://redis:6379/1"
    CELERY_RESULT_BACKEND: str = "redis://redis:6379/2"

    # ------------------------------------------------------- LLM (Gemini) --
    GEMINI_API_KEY: str = "AIzaSyDedjmn8hxYh65ySRyFHjG3IyuXp7Zc4mg"
    GEMINI_MODEL: str = "gemini-2.5-flash"

    # ------------------------------------- Local embeddings / vector store --
    EMBEDDING_MODEL_NAME: str = "BAAI/bge-small-en-v1.5"
    EMBEDDING_DIM: int = 384
    FAISS_INDEX_DIR: str = "/data/faiss"

    # -------------------------------------------------- File uploads / OCR --
    UPLOAD_DIR: str = "/data/uploads"
    PROCESSED_DIR: str = "/data/uploads/processed"
    MAX_UPLOAD_MB: int = 200
    ALLOWED_FILE_TYPES: str = "pdf,docx,doc,txt,png,jpg,jpeg,tiff,bmp"

    # -------------------------------------------------------------- server --
    FASTAPI_HOST: str = "0.0.0.0"
    FASTAPI_PORT: int = 8001

    # --------------------------------- JWT (shared with Django service) ----
    JWT_SIGNING_KEY: str = "change-me-shared-with-django"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: str = "django-service"
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    # ----------------------------------------------------------------- CORS
    CORS_ORIGINS: str = "http://localhost:3000,http://localhost:8000"

    # ---------------------------------------------------- Rate limiting ----
    RATE_LIMIT_PER_MINUTE: int = 60

    # ---------------------------------------------------------- Chunking ---
    CHUNK_SIZE_TOKENS: int = 512
    CHUNK_OVERLAP_TOKENS: int = 128
    MIN_CHUNK_TOKENS: int = 50

    # --------------------------------------------------------------- RAG ---
    RAG_TOP_K: int = 5
    RAG_SCORE_THRESHOLD: float = 0.35  # cosine similarity floor for FAISS+SBERT
    RAG_MAX_CONTEXT_TOKENS: int = 6000
    # Phase 3 RAG knobs
    RAG_PER_QUERY_TOP_K: int = 20
    RAG_RRF_K: int = 60
    RAG_RERANK_TOP_K: int = 8
    RAG_FINAL_K: int = 6
    RAG_MMR_LAMBDA: float = 0.5
    RAG_TOTAL_TOKEN_BUDGET: int = 28000
    RAG_RESERVE_SYSTEM_TOKENS: int = 1000
    RAG_RESERVE_HISTORY_TOKENS: int = 1000
    RAG_RESERVE_ANSWER_TOKENS: int = 1500
    RAG_HISTORY_MAX_TURNS: int = 6
    RAG_ENABLE_RERANK: bool = True
    RAG_QUERY_EXPANSION: bool = True
    RAG_QUERY_CACHE_TTL: int = 3600
    RAG_EVAL_ENABLED: bool = True
    RAG_EVAL_SAMPLE_LIMIT: int = 50
    RAG_GROUNDING_MIN_SCORE: float = 0.35
    DOCUMENT_CLASSIFICATION_MIN_CONFIDENCE: float = 0.55
    RERANKER_MODEL_NAME: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    # LLM generation
    LLM_TEMPERATURE: float = 0.2
    LLM_TOP_P: float = 0.9
    LLM_MAX_OUTPUT_TOKENS: int = 1500
    LLM_REQUEST_TIMEOUT_SECONDS: int = 120
    LLM_CIRCUIT_FAIL_THRESHOLD: int = 5
    LLM_CIRCUIT_WINDOW_SECONDS: int = 30
    LLM_CIRCUIT_OPEN_SECONDS: int = 60

    # Chat rate limiting (per-user, redis token bucket)
    CHAT_RATE_LIMIT_PER_MINUTE: int = 20
    CHAT_RATE_LIMIT_BURST: int = 5

    # Sentry
    SENTRY_DSN: str = ""

    # -------------------------------------------------------- Convenience --
    @property
    def database_url(self) -> str:
        return self.DATABASE_URL

    @property
    def sync_database_url(self) -> str:
        return self.SYNC_DATABASE_URL

    @property
    def allowed_file_types_list(self) -> List[str]:
        return [t.strip().lower() for t in self.ALLOWED_FILE_TYPES.split(",") if t.strip()]

    @property
    def cors_origins_list(self) -> List[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def max_file_size_bytes(self) -> int:
        return self.MAX_UPLOAD_MB * 1024 * 1024

    # Back-compat alias used by existing routes.
    @property
    def MAX_FILE_SIZE_MB(self) -> int:  # noqa: N802
        return self.MAX_UPLOAD_MB


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
