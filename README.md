# Legal Document Intelligence Platform

AI-powered legal document intelligence platform for ingesting, processing,
retrieving, and interacting with contracts, legal text, and workspace-based
document workflows.

Production-grade monorepo containing:

- **fastapi_service/** — FastAPI 0.111 + SQLAlchemy 2 (async) + Celery + FAISS + sentence-transformers + Gemini LLM.
- **django_service/** — Django 5 + DRF + drf-spectacular + Channels + Daphne. Auth, workspaces, chat orchestration.
- **scripts/create_multiple_dbs.sh** — Postgres multi-DB bootstrap.
- **docker-compose.yml / docker-compose.prod.yml** — full local + prod orchestration.

> The legacy top-level `app/` folder is a prototype and is **ignored** by every build (`.dockerignore`). Do not modify.

## Architecture (high level)

```
┌──────────────┐     ┌────────────┐    ┌──────────────┐
│   Django     │────▶│  FastAPI   │───▶│ Celery (OCR, │
│  (DRF, JWT,  │ HTTP│  (RAG,     │     │ embed,       │
│   Channels)  │     │  uploads)  │     │ FAISS)       │
└──────┬───────┘     └─────┬──────┘     └──────┬───────┘
       │ JWT (shared key)  │                   │
       ▼                   ▼                   ▼
   Postgres (django_db, fastapi_db)        Redis 7
                                           FAISS on disk
                                           sentence-transformers (local)
                                           Gemini 1.5 Flash (only external API)
```

## Quickstart

1. **Copy env template and fill in secrets**
   ```bash
   cp .env.example .env
   # edit .env: set GEMINI_API_KEY, JWT_SIGNING_KEY, DJANGO_SECRET_KEY
   ```

2. **Build & start everything**
   ```bash
   make up
   ```

3. **Run migrations** (Alembic for FastAPI, Django migrate for Django)
   ```bash
   make migrate
   ```

4. **Verify health**
   - FastAPI:  http://localhost:8001/health
   - FastAPI metrics:  http://localhost:8001/metrics
   - FastAPI docs:  http://localhost:8001/docs
   - Django:   http://localhost:8000/healthz/
   - Django docs:  http://localhost:8000/api/docs/
   - Django admin:  http://localhost:8000/admin/

5. **Create a Django superuser** (optional)
   ```bash
   make createsuperuser
   ```

## Hard constraints (apply to every phase)

- **LLM**: Gemini 1.5 Flash via `google-generativeai` (only external API).
- **Embeddings**: LOCAL via `sentence-transformers`, model `BAAI/bge-small-en-v1.5` (384-dim).
- **Vector store**: LOCAL FAISS (`faiss-cpu`), one index per document under `${FAISS_INDEX_DIR}/{document_id}.index`.
- **OCR**: LOCAL only (`pdfplumber`, `pytesseract`, `pdf2image`, `python-docx`, `Pillow`, `chardet`).
- Two services in compose: `fastapi` (8001), `django` (8000 via Daphne ASGI).
- Two Postgres DBs: `fastapi_db`, `django_db` (same Postgres 15 cluster).
- Redis 7 for Celery broker + Django Channels + SSE pub/sub.
- Python 3.11 base images, non-root containers, healthchecks, `.dockerignore`.

## Make targets

`make help` — show all targets. Common ones: `up`, `down`, `logs`, `migrate`, `createsuperuser`, `shell-fastapi`, `shell-django`, `test`, `lint`, `fmt`.
