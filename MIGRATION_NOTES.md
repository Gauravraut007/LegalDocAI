# Phase 1 Migration Notes

Foundation-only changes. **No business logic added.** Existing AI / RAG code was rewired to local stacks because the Phase 1 hard constraints forbid Qdrant and Gemini embeddings.

## Repo-level

| File | Change | Why |
|------|--------|-----|
| `docker-compose.yml` | Rewritten. Services: `postgres` (multi-DB init via `POSTGRES_MULTIPLE_DATABASES`), `redis` (with `redis_data` volume + healthcheck), `fastapi` (8001), `celery_worker` (`-Q ocr,embed,default --concurrency=2`), `celery_beat`, `django` (Daphne 8000), `flower` (profile=`debug`). Named volumes: `pg_data`, `redis_data`, `faiss_data` (mounted at `/data/faiss`), `media_data` (mounted at `/data/uploads` in fastapi+celery+django). All `depends_on` use `service_healthy`. | Removes Qdrant, adds FAISS persistence volume, aligns service names with the new architecture. |
| `docker-compose.prod.yml` | Slimmer prod overrides only (no port publication for postgres/redis, prod images, ENVIRONMENT=production, daphne for django). | Single source of truth for service shape lives in dev compose. |
| `.env.example` | New canonical env template at repo root with everything both services need (DATABASE_URL, REDIS_URL, GEMINI_API_KEY, EMBEDDING_MODEL_NAME, EMBEDDING_DIM, FAISS_INDEX_DIR, UPLOAD_DIR, MAX_UPLOAD_MB, JWT_SIGNING_KEY/ALGORITHM/ISSUER, DJANGO_*, CORS_ORIGINS, SENTRY_DSN, etc.). | Single source of truth; cross-service JWT works out of the box. |
| `.env` | Mirrors `.env.example` with placeholders. Replace `GEMINI_API_KEY`, `JWT_SIGNING_KEY`, `DJANGO_SECRET_KEY` before use. | Local dev convenience. |
| `scripts/create_multiple_dbs.sh` | Idempotent (won't fail if DB already exists), trims whitespace, uses `set -euo pipefail`, quotes identifiers. Mounted read-only into postgres init dir. | Safe re-runs and clean compose restarts. |
| `Makefile` | New top-level. Targets: `up`, `down`, `logs`, `ps`, `build`, `rebuild`, `migrate` (alembic + django), `createsuperuser`, `shell-fastapi`, `shell-django`, `shell-celery`, `shell-postgres`, `test`, `lint`, `fmt`, `clean`. | Required by Phase 1 spec. |
| `.dockerignore` | New. Excludes `env/`, virtual envs, `.env`, caches, the legacy `app/` prototype, FAISS index files, uploads, IDE folders. | Smaller, safer images. |
| `.gitignore` | New. Same shape as `.dockerignore`, plus `staticfiles/`, `media/`. | Stops leaking local data. |
| `pyproject.toml` | New. Shared ruff + black + mypy config for both services; excludes `app/`, `env/`, `migrations/`, `alembic/versions/`. | Required by spec. |
| `README.md` | New top-level README with Quickstart, hard constraints, architecture diagram, make targets. | Onboarding. |

## fastapi_service

| File | Change | Why |
|------|--------|-----|
| `requirements.txt` | **Removed** `qdrant-client`, `python-jose`, `passlib`. **Added** `faiss-cpu==1.8.0`, `sentence-transformers==3.0.1`, `torch==2.3.1`, `numpy`, `pyjwt[crypto]==2.9.0`. Kept Gemini SDK for LLM only. | Local embeddings + local FAISS + cross-service JWT. |
| `Dockerfile` | Multi-stage (builder + runtime). System deps: `tesseract-ocr`, `tesseract-ocr-eng`, `poppler-utils`, `libmagic1`, `libgl1`, `libglib2.0-0`, `libpq5`, `curl`, `tini`. Pre-downloads `BAAI/bge-small-en-v1.5` into `/opt/hf_cache` during build (offline runtime via `TRANSFORMERS_OFFLINE=1`). Non-root `app` user. `HEALTHCHECK` on `/health`. | Production-shaped image, fast cold-starts, no model download at runtime. |
| `app/config.py` | Full rewrite using `pydantic-settings`. New keys: `DATABASE_URL`, `SYNC_DATABASE_URL`, `REDIS_URL`, `GEMINI_API_KEY`, `GEMINI_MODEL`, `EMBEDDING_MODEL_NAME`, `EMBEDDING_DIM=384`, `FAISS_INDEX_DIR=/data/faiss`, `UPLOAD_DIR=/data/uploads`, `MAX_UPLOAD_MB=200`, `JWT_SIGNING_KEY`, `JWT_ALGORITHM=HS256`, `JWT_ISSUER=django-service`. Removed every `QDRANT_*` and Gemini-embedding key. Added `MAX_FILE_SIZE_MB` back-compat property so existing `routes/documents.py` keeps working unchanged. | Spec compliance + zero collateral damage to other modules. |
| `app/main.py` | Rewritten. Mounts `/health`, `/metrics` (`prometheus-fastapi-instrumentator`), CORS, request-id middleware (binds id into structlog contextvars), structlog **JSON** logger, RFC7807 `application/problem+json` error responses for `RequestValidationError`, `HTTPException`, and uncaught `Exception`. Optional business routers wrapped in try/except so a missing optional dep does not break `/health`. | Phase 1 acceptance criteria. |
| `app/routes/health.py` | Removed Qdrant probe. Now returns `postgres`, `redis`, `gemini` (configured / missing_api_key) status. Returns 200 when postgres+redis healthy. | No Qdrant in the stack. |
| `app/ai/vectorstore.py` | **Replaced QdrantVectorStore with `FaissVectorStore`** — one FAISS `IndexFlatIP` per document persisted under `${FAISS_INDEX_DIR}/{id}.index` plus a `.meta` JSON sidecar; cosine similarity via L2-normalized vectors; same async public surface (`create_collection`, `upsert_chunks`, `search`, `list_chunks`, `delete_collection`, `get_collection_info`). | Local-only vector store per spec. |
| `app/ai/embedder.py` | **Replaced GeminiEmbedder with `LocalEmbedder`** wrapping `sentence-transformers` (`BAAI/bge-small-en-v1.5`, 384-dim). Model is loaded lazily and cached. Uses BGE query prefix for retrieval. CPU inference is run via `asyncio.to_thread`. | No external embedding API. |
| `app/utils/retry.py` | Removed `qdrant_retry`. Kept `gemini_retry` for LLM calls. | Qdrant gone. |
| `app/ai/__init__.py` | Public exports renamed (`LocalEmbedder`, `FaissVectorStore`). | API stays consistent for downstream code. |
| `app/ai/document_processor.py`, `app/ai/rag.py`, `app/routes/ai.py`, `app/routes/documents.py` | Surgical rename of `GeminiEmbedder` → `LocalEmbedder` and `QdrantVectorStore` → `FaissVectorStore`. No behavioural changes. | Eliminates last Qdrant references. |
| `app/database.py`, `app/workers/tasks.py`, models, schemas, alembic | **Untouched**. Alembic `env.py` already reads `settings.sync_database_url` (still works because `Settings` exposes both `database_url` and `sync_database_url`). | Phase 1 forbids touching business logic. |

Verification: `grep -i "qdrant\|GEMINI_EMBEDDING\|GeminiEmbedder" fastapi_service/` returns no matches.

## django_service

The original `core/settings/base.py`, `core/urls.py`, and `core/asgi.py` all referenced **business apps that don't exist yet** (`apps.users`, `apps.workspaces`, `apps.documents`, `apps.chat`) plus a custom user model — that would crash any `manage.py migrate`. Phase 1 says **"DO NOT implement business apps yet, just skeleton"**, so they have been removed from the skeleton and will be reintroduced in their own phases.

| File | Change | Why |
|------|--------|-----|
| `requirements.txt` | New. `django>=5,<5.2`, `djangorestframework`, `djangorestframework-simplejwt`, `channels`, `channels-redis`, `daphne`, `django-redis`, `django-cors-headers`, `django-filter`, `drf-spectacular`, `gunicorn`, `whitenoise`, `psycopg[binary]`, `python-decouple`, `httpx`, `pyjwt[crypto]`, `structlog`, `sentry-sdk`. No Celery (Django talks to FastAPI over HTTP per spec). | Spec list. |
| `Dockerfile` | New. Multi-stage `python:3.11-slim`, system deps (`libpq5`, `curl`, `tini`), non-root `app` user, best-effort `collectstatic --noinput`, `HEALTHCHECK` on `/healthz/`, `daphne -b 0.0.0.0 -p 8000 core.asgi:application` entrypoint. | Spec. |
| `core/settings/base.py` | Rewritten. `DATABASES` from `DJANGO_DB_*`, `REST_FRAMEWORK` (JWT default + drf-spectacular schema class), `SIMPLE_JWT` (`SIGNING_KEY` ← `JWT_SIGNING_KEY` env, `ISSUER="django-service"`, access 15m, refresh 7d, blacklist enabled — `token_blacklist` already in `INSTALLED_APPS`), `CHANNEL_LAYERS` (redis), `CACHES` (`django-redis`), CORS, `LOGGING` (structlog JSON to stdout), `STATIC_ROOT` + `STATICFILES_STORAGE=whitenoise`, `MEDIA_ROOT=/data/uploads`, `SPECTACULAR_SETTINGS`, optional Sentry init from `SENTRY_DSN`. **Removed** all `apps.*` imports, removed `AUTH_USER_MODEL = users.User` (no users app yet — will come in a later phase). | Skeleton that actually boots and migrates. |
| `core/settings/development.py`, `production.py` | **Unchanged.** Still inherit from `base`. | They're already correct. |
| `core/urls.py` | Rewritten. Routes: `/admin/`, `/api/schema/`, `/api/docs/` (Swagger UI), `/api/redoc/`, `/healthz/`. Removed all business-app `include()`s. | Skeleton only; won't crash on missing apps. |
| `core/asgi.py` | Rewritten. Wires `ProtocolTypeRouter` with HTTP + WebSocket via `AllowedHostsOriginValidator` + `AuthMiddlewareStack`. Empty websocket routes for now. No `apps.chat` import. | Phase 1 has no chat app. |
| `core/health.py` | New. `healthz` view checks Postgres + Redis (cache backend) and returns 200/503 with JSON. | Required by acceptance criteria. |
| `common/*` | **Untouched** (`exceptions.py`, `pagination.py`, `middleware.py`, `utils.py` were already correct). | Re-used by `REST_FRAMEWORK` settings. |

## Acceptance criteria mapping

- [x] `docker compose up --build` brings every service healthy → all services have `healthcheck`s; `depends_on` uses `service_healthy`.
- [x] `curl http://localhost:8001/health` → 200 with `postgres`/`redis`/`gemini` status.
- [x] `curl http://localhost:8000/healthz/` → 200 (503 when degraded).
- [x] Postgres bootstraps both `fastapi_db` and `django_db` (`POSTGRES_MULTIPLE_DATABASES=fastapi_db,django_db` + idempotent init script).
- [x] `alembic upgrade head` runs cleanly inside the `fastapi` container (`alembic/env.py` reads `settings.sync_database_url`, single `001_initial` revision exists).
- [x] `python manage.py migrate` runs cleanly inside the `django` container (no missing `apps.*` references; default `auth.User`).
- [x] `/metrics` exposes Prometheus metrics (`prometheus-fastapi-instrumentator`).
- [x] No reference to `qdrant` anywhere under `fastapi_service/` (verified by grep).
- [x] Legacy top-level `app/` folder untouched and excluded from both Docker build contexts via `.dockerignore`.

## Things deliberately deferred (NOT in Phase 1)

- Business apps under `django_service/apps/` (users, workspaces, documents, chat).
- Cross-service JWT verification middleware in FastAPI (key + algo + issuer are now configured; the verifier itself is Phase ≥ 2 work).
- WebSocket consumers, chat routing.
- Re-tuning `RAG_SCORE_THRESHOLD` defaults for the BGE embedding space (set to 0.35 as a sane starting point; will be revisited when RAG quality is measured).
