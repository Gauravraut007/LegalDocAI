# BUILD_SPEC — Legal Document Intelligence Platform (LDIP)

> **For the AI coding assistant:** this file is a complete specification. Build the project described here from scratch, in a new empty repo, phase by phase. After each phase, run the acceptance checks before moving on. Ask the human only for: a Gemini API key, and confirmation of OS (Windows/Mac/Linux). Never hardcode secrets; read everything from environment variables.

## 1. What you are building

A multi-tenant web app where users upload legal documents (contracts, NDAs, court filings), the system ingests them (OCR → chunk → embed → index), and users chat with the documents. Answers are streamed, grounded in the documents, and cite their sources (`[S1]`, `[S2]`…). Users belong to **workspaces** with roles and invitations.

Two Python services behind Docker Compose:

| Service | Stack | Role |
|---|---|---|
| `django_service` | Django 5 + DRF + simplejwt + Channels + Daphne | Public API + server-rendered web UI. Auth, users, workspaces, permission checks, gateway to FastAPI, WebSocket bridge. |
| `fastapi_service` | FastAPI + SQLAlchemy 2 (async) + Alembic + Celery | Upload, ingestion pipeline, RAG, chat streaming. Verifies Django-issued JWTs locally. |

Infra: Postgres 15 (two DBs: `django_db`, `fastapi_db`), Redis 7 (Celery broker/result, Channels layer, SSE pub/sub, caches, rate limit), local disk volumes for uploads and FAISS indexes.

```
browser ─JWT─▶ Django (DRF / Channels / templates) ─JWT─▶ FastAPI ─▶ Celery workers (OCR, embed)
                  │ mirror tables, perms                     │ RAG, SSE            │
                  ▼                                          ▼                     ▼
              Postgres django_db                        Postgres fastapi_db   FAISS files + uploads
                          Redis 7 (broker, channels, pubsub)       Gemini (only external API)
```

### Hard constraints
- **LLM:** Google Gemini via `google-generativeai`. This is the **only** external API. Model name comes from env `GEMINI_MODEL` (default `gemini-2.5-flash`; check the current available Flash model, older 1.5 names are retired).
- **Embeddings:** local `sentence-transformers`, model `BAAI/bge-small-en-v1.5` (384-dim, L2-normalised).
- **Vector store:** local FAISS (`faiss-cpu`), **one index per document**: `{FAISS_INDEX_DIR}/{document_id}.index` + `{document_id}.meta.json`. `IndexFlatIP`; switch to HNSW above 50k vectors.
- **OCR/parsing:** local only: `pdfplumber`, `pytesseract`, `pdf2image`, `python-docx`, `Pillow`, `chardet`, `python-magic`. (Tesseract + Poppler must be installed in the Docker image.)
- Python 3.11 base images, non-root containers, healthchecks, `.dockerignore` per service.
- The browser never talks to FastAPI directly. Django forwards the user's bearer token unchanged.

## 2. Repository layout

```
.
├── docker-compose.yml            # dev
├── docker-compose.prod.yml       # prod overrides (no published db ports, restart: always, 4 uvicorn workers)
├── .env.example  .gitignore  .dockerignore  Makefile  README.md
├── scripts/create_multiple_dbs.sh   # postgres init: creates each DB in POSTGRES_MULTIPLE_DATABASES
├── fastapi_service/
│   ├── Dockerfile  requirements.txt  alembic.ini
│   ├── alembic/versions/ (initial + pipeline + chat migrations)
│   ├── app/
│   │   ├── main.py  config.py  database.py
│   │   ├── models/{document.py,chat.py}
│   │   ├── schemas/{document.py,chat.py}
│   │   ├── routes/{documents.py,chat.py,health.py}
│   │   ├── security/{jwt.py,rate_limit.py}
│   │   ├── ai/{ocr_engine,chunker,classifier,embedder,vectorstore,retriever,llm,prompts,rag,evaluation}.py
│   │   ├── utils/{file_handler,events,locks,metrics,retry,batch_processor}.py
│   │   └── workers/tasks.py      # Celery app + pipeline tasks
│   └── tests/
└── django_service/
    ├── Dockerfile  requirements.txt  manage.py  pytest.ini
    ├── core/{settings/{base,development,production,test}.py, urls.py, asgi.py, wsgi.py, health.py}
    ├── common/{channels_auth,exceptions,middleware,pagination,permissions,utils}.py
    ├── apps/
    │   ├── users/       # custom user, JWT claims, throttles, validators, seed_demo command
    │   ├── workspaces/  # Workspace, WorkspaceMember, Invitation + permissions
    │   ├── documents/   # mirror model, views, services/fastapi_client.py, WS consumer
    │   ├── chat/        # mirror model, views, WS consumer (SSE→WS bridge)
    │   └── web/         # server-rendered UI: views, templates, static css
    └── tests/
```

## 3. Environment variables (`.env.example`)

Use placeholders only; never commit `.env`.

```
ENVIRONMENT=development
POSTGRES_USER=postgres
POSTGRES_PASSWORD=<choose>
POSTGRES_MULTIPLE_DATABASES=fastapi_db,django_db
FASTAPI_DB_NAME=fastapi_db
DJANGO_DB_NAME=django_db  DJANGO_DB_USER=postgres  DJANGO_DB_PASSWORD=<same>  DJANGO_DB_HOST=postgres  DJANGO_DB_PORT=5432
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/1
CELERY_RESULT_BACKEND=redis://redis:6379/2
GEMINI_API_KEY=<required>            # no default in code
GEMINI_MODEL=gemini-2.5-flash
EMBEDDING_MODEL_NAME=BAAI/bge-small-en-v1.5
EMBEDDING_DIM=384
FAISS_INDEX_DIR=/data/faiss
UPLOAD_DIR=/data/uploads
PROCESSED_DIR=/data/uploads/processed
MAX_UPLOAD_MB=200
ALLOWED_FILE_TYPES=pdf,docx,doc,txt,png,jpg,jpeg,tiff,bmp
JWT_SIGNING_KEY=<random ≥64 bytes, SHARED by both services>
JWT_ALGORITHM=HS256  JWT_ISSUER=django-service
JWT_ACCESS_TOKEN_EXPIRE_MINUTES=15  JWT_REFRESH_TOKEN_EXPIRE_DAYS=7
DJANGO_SECRET_KEY=<random>  DJANGO_DEBUG=True
DJANGO_SETTINGS_MODULE=core.settings.development
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1,django,fastapi
FASTAPI_BASE_URL=http://fastapi:8001   FASTAPI_TIMEOUT_SECONDS=120
CORS_ORIGINS=http://localhost:3000,http://localhost:8000,http://localhost:8001
SENTRY_DSN=   LOG_LEVEL=INFO   RATE_LIMIT_PER_MINUTE=60
```

`DATABASE_URL` / `SYNC_DATABASE_URL` / Redis URLs / `FAISS_INDEX_DIR` etc. are set in the compose `environment:` block (`postgresql+asyncpg://…` for async, `postgresql+psycopg2://…` for Alembic/Celery).

FastAPI `Settings` (pydantic-settings) also carries all RAG/LLM knobs with these defaults:

```
CHUNK_SIZE_TOKENS=512  CHUNK_OVERLAP_TOKENS=128  MIN_CHUNK_TOKENS=50
RAG_TOP_K=5  RAG_SCORE_THRESHOLD=0.35
RAG_PER_QUERY_TOP_K=20  RAG_RRF_K=60  RAG_RERANK_TOP_K=8  RAG_FINAL_K=6  RAG_MMR_LAMBDA=0.5
RAG_TOTAL_TOKEN_BUDGET=28000  RAG_RESERVE_SYSTEM_TOKENS=1000  RAG_RESERVE_HISTORY_TOKENS=1000  RAG_RESERVE_ANSWER_TOKENS=1500
RAG_HISTORY_MAX_TURNS=6  RAG_ENABLE_RERANK=true  RAG_QUERY_EXPANSION=true  RAG_QUERY_CACHE_TTL=3600
RAG_EVAL_ENABLED=true  RAG_EVAL_SAMPLE_LIMIT=50  RAG_GROUNDING_MIN_SCORE=0.35
DOCUMENT_CLASSIFICATION_MIN_CONFIDENCE=0.55
RERANKER_MODEL_NAME=cross-encoder/ms-marco-MiniLM-L-6-v2
LLM_TEMPERATURE=0.2  LLM_TOP_P=0.9  LLM_MAX_OUTPUT_TOKENS=1500  LLM_REQUEST_TIMEOUT_SECONDS=120
LLM_CIRCUIT_FAIL_THRESHOLD=5  LLM_CIRCUIT_WINDOW_SECONDS=30  LLM_CIRCUIT_OPEN_SECONDS=60
CHAT_RATE_LIMIT_PER_MINUTE=20  CHAT_RATE_LIMIT_BURST=5
```

## 4. Dependencies (pin these versions; they are known to work together)

**fastapi_service/requirements.txt**
```
fastapi==0.111.0  uvicorn[standard]==0.29.0  python-multipart==0.0.9
sqlalchemy[asyncio]==2.0.30  asyncpg==0.29.0  psycopg2-binary==2.9.9  alembic==1.13.1  greenlet==3.0.3
pydantic==2.7.1  pydantic-settings==2.3.0  pyjwt[crypto]==2.9.0
httpx==0.27.0  tenacity==8.3.0  aiofiles==24.1.0
celery[redis]==5.4.0  redis==5.0.6
google-generativeai==0.7.2
sentence-transformers==3.0.1  torch==2.3.1  faiss-cpu==1.8.0  numpy==1.26.4
tiktoken==0.7.0  pysbd==0.3.4  langdetect==1.0.9
pdfplumber==0.11.0  pdf2image==1.17.0  pytesseract==0.3.10  python-docx==1.1.2  Pillow==10.3.0  chardet==5.2.0
python-magic==0.4.27  python-magic-bin==0.4.14; sys_platform == "win32"
structlog==24.2.0  prometheus-fastapi-instrumentator==7.0.0  prometheus-client==0.20.0
pytest==8.3.2  pytest-asyncio==0.23.8  pytest-cov==5.0.0  pytest-mock==3.14.0  reportlab==4.2.2
```
If `google-generativeai` / Gemini model names have moved on, use the current supported SDK but keep the same internal interface (`stream(prompt, system) -> async iterator of text chunks`).

**django_service/requirements.txt**
```
django>=5.0,<5.2  djangorestframework==3.15.2  djangorestframework-simplejwt==5.3.1
channels==4.1.0  channels-redis==4.2.0  daphne==4.1.2  django-redis==5.4.0
django-cors-headers==4.4.0  django-filter==24.2  drf-spectacular==0.27.2  gunicorn==22.0.0  whitenoise==6.7.0
psycopg[binary]==3.2.1  python-decouple==3.8  httpx==0.27.0  pyjwt[crypto]==2.9.0
structlog==24.2.0  sentry-sdk==2.13.0
pytest==8.3.2  pytest-django==4.9.0  pytest-asyncio==0.23.8  pytest-mock==3.14.0
```

## 5. Docker Compose services

`postgres` (15-alpine, init script creates both DBs, healthcheck `pg_isready`), `redis` (7-alpine, `--appendonly yes`, healthcheck `redis-cli ping`), `fastapi` (port 8001, healthcheck `curl /health`), `celery_worker` (same image as fastapi; `celery -A app.workers.tasks worker -l info -Q ocr,embed,default --concurrency=2`), `celery_beat`, `django` (port 8000, `daphne -b 0.0.0.0 -p 8000 core.asgi:application`), `flower` (optional, profile `debug`, port 5555). Named volumes: `pg_data`, `redis_data`, `faiss_data` (`/data/faiss`), `media_data` (`/data/uploads`) — mounted into **both** fastapi and celery_worker. `depends_on` with `condition: service_healthy`. Entry scripts for Django should run `migrate` and `collectstatic`; FastAPI runs `alembic upgrade head`.

Makefile targets: `up, down, restart, logs, ps, build, rebuild, migrate, makemigrations, createsuperuser, shell-fastapi, shell-django, shell-celery, shell-postgres, test, lint, fmt, clean`.

## 6. Auth contract (Django is the identity source of truth)

- Custom user model with **UUID pk**, email as login (`USERNAME_FIELD=email`), `full_name`, `avatar`, `is_staff`. Custom manager.
- JWT via simplejwt: HS256, `JWT_SIGNING_KEY`, issuer `django-service`, access 15 min, refresh 7 days, **rotate + blacklist** refresh tokens.
- Custom token serializer adds claims: `user_id` (UUID str), `email`, `is_staff`, `workspace_ids` (list of UUID strs from `WorkspaceMember`, computed at issue time).
- FastAPI `security/jwt.py` `get_current_user` dependency verifies locally: signature, `iss`, `exp`, all required claims present, `user_id` and each `workspace_ids[i]` parse as UUID. Returns a `CurrentUser` dataclass. 401 on failure.
- **Document access rule (FastAPI):** user may access a document iff `owner_user_id == user_id` OR `workspace_id ∈ workspace_ids` OR `is_staff`. Apply to every document and chat route.
- REST endpoints under `/api/v1/auth/`: `register` (creates user + default personal workspace), `login`, `token/refresh`, `logout` (body `{refresh}` → blacklist), `me` (GET/PATCH), `password/change`, `password/reset/request` (always 202, no enumeration), `password/reset/confirm` (token TTL 1h).
- Password policy: min length 12, Django common/numeric/similarity validators + custom `ComplexityValidator` (upper, lower, digit, symbol).
- Throttles: anon 20/min, user 120/min, login 5/min, register 10/min, password_reset 5/min (custom scoped throttle classes).
- WebSocket auth middleware (`common/channels_auth.py`): token from `?token=`, or `Sec-WebSocket-Protocol: bearer, <JWT>`, or `Authorization` header. Reject with close code `4401` if invalid.

## 7. Workspaces (Django)

Models (all UUID pks):
- `Workspace(name, slug unique auto, owner FK, plan [free/pro/enterprise], timestamps)`
- `WorkspaceMember(workspace, user, role [owner/admin/member/viewer], invited_at, joined_at)` unique on (workspace, user)
- `Invitation(workspace, email, role, token unique 64 chars, status [pending/accepted/revoked/expired], invited_by, created_at, expires_at default +7d)`

REST: CRUD workspaces, list/update/remove members, create/revoke invitations, `invitations/accept/{token}/`. Permission classes enforce role hierarchy (owner > admin > member > viewer; viewers read-only). Signup creates a personal workspace with the user as owner. Add a `seed_demo` management command that creates a demo user + workspace.

## 8. FastAPI data model (Postgres `fastapi_db`, Alembic migrations)

Enums: `DocType{contract,nda,filing,general,unknown}`, `DocStatus{uploaded,validating,ocr,chunking,embedding,indexing,ready,failed}`, `VersionKind{original,ocr_text,normalized}`, `JobState{queued,running,succeeded,failed,cancelled}`, `MessageRole{user,assistant,system}`.

Tables (UUID pks, timestamptz `created_at`/`updated_at`):
- `documents`: owner_user_id, workspace_id (nullable), original_filename, stored_path, mime_type, size_bytes, sha256, doc_type, status, error_message, page_count, language, extra JSONB. **Unique (owner_user_id, sha256)** → duplicate upload detection. Indexes on owner, workspace, status, doc_type, created_at.
- `document_versions`: document_id FK cascade, kind, storage_path; unique (document_id, kind).
- `chunks`: document_id FK cascade, ordinal, page_start, page_end, char_start, char_end, token_count, text, section_path, hash(sha256); unique (document_id, ordinal) and (document_id, hash).
- `embeddings_meta`: document_id FK cascade, model_name, dim, faiss_index_path, vector_count; unique (document_id, model_name).
- `ingest_jobs`: document_id FK cascade, celery_task_id, state, progress_pct, stage, message, started_at, finished_at.
- `chat_sessions`: user_id, workspace_id, title (default "New chat"), `document_ids` JSONB list, timestamps.
- `chat_messages`: session_id FK cascade, role, content, `sources` JSONB, tokens_in, tokens_out, latency_ms, model_name, created_at.

Django mirror tables (thin; FastAPI is source of truth for status/doc_type/page_count/sha256): `Document(id = FastAPI id, workspace FK, uploaded_by FK, title, status, doc_type, page_count, sha256, …)` and `ChatSession(id = FastAPI id, workspace, user, title, …)`. Lazy-pull: every GET refreshes the mirror from FastAPI. **Deletion is upstream-first** (call FastAPI DELETE; remove mirror only on success or 404).

## 9. Ingestion pipeline (FastAPI + Celery)

Upload `POST /api/v1/documents/upload` (multipart): validate extension against `ALLOWED_FILE_TYPES` and real MIME via python-magic, enforce `MAX_UPLOAD_MB`, **stream to disk** while computing sha256, store at `/data/uploads/{owner_id}/{document_id}/original.{ext}`, reject duplicates (owner+sha256), create `Document` + `IngestJob`, launch the Celery chain, return 202 with the document.

Chain of 5 tasks (`workers/tasks.py`), each **idempotent, resumable, retried with exponential backoff**:

| # | Task | Queue | Work | max_retries |
|---|---|---|---|---|
| 1 | `validate_document` | default | status→validating, acquire lock, sanity checks | 3 |
| 2 | `ocr_document` | ocr | extract text per page (PDF: pdfplumber first, fall back to pdf2image+Tesseract for pages with little/no text; DOCX via python-docx; TXT with chardet; images via Tesseract). Write `ocr.txt` + `ocr_spans.json` (per-page char spans) atomically. Detect language (langdetect). Run classifier → `doc_type`. Upsert `DocumentVersion(ocr_text)`. Set page_count, language. | 2 |
| 3 | `chunk_document` | default | legal-aware chunking (below). Delete prior chunks, insert new. | 2 |
| 4 | `embed_document` | embed | embed chunk texts in batches, build FAISS index, write to `.tmp` then atomic replace, write `.meta.json` `{chunk_ids, model_name, dim, type, count}`, upsert `embeddings_meta`. Skip if `vector_count == len(chunks)` and file exists. | 3 |
| 5 | `index_document` | default | verify FAISS file, set `status=ready`, `IngestJob.succeeded` | 2 |

On any failure: `status=failed`, write `error_message`, job `failed`, increment failure counter, publish a `failed` event.

**Concurrency:** per-document Postgres advisory lock `pg_try_advisory_lock(hashtext(uuid))` (`utils/locks.py`) so one document never runs in parallel.

**Progress events:** every stage publishes JSON `{stage, progress_pct, message, ts, extra}` to Redis channel `ingest:{document_id}` and stores the latest at key `ingest:{document_id}:latest` (TTL 1h). SSE endpoint `GET /api/v1/documents/{id}/events` replays the latest snapshot, subscribes, emits `event: progress` frames, `event: end` on `ready`/`failed`, and a `: keep-alive` comment every 15 s.

Other document routes: list (filters, pagination), get, delete (remove DB rows, files, FAISS index), re-process.

### Chunker (`ai/chunker.py`)
1. Split text on legal section headers via regex (Article/Section/Clause/numbered headings), tracking `section_path` such as `"Article 3 > Section 3.2 > (a)"`.
2. Within sections, split sentences with `pysbd`.
3. Pack sentences to ~512 tokens (tiktoken `cl100k_base`; fall back to chars/4) with 128-token overlap; never split a sentence; hard cap 1.6× target; drop chunks under `MIN_CHUNK_TOKENS`.
4. Map char offsets → page numbers via OCR page spans.
5. Drop empty and duplicate (sha256) chunks.

### Classifier (`ai/classifier.py`)
Rule-based: regex pattern sets for NDA, court filing, contract, and general-legal hints, applied to the first ~8000 chars of OCR text. Returns `ClassificationResult(doc_type, confidence, reason)`. **Confidence gating:** if confidence < `DOCUMENT_CLASSIFICATION_MIN_CONFIDENCE` (0.55) store `unknown` (or `general` if legal hints present) rather than guessing.

### Embedder / vector store
Singleton lazy-loaded SentenceTransformer, `normalize_embeddings=True`, batch encoding in a thread. For BGE, prefix queries with `"Represent this sentence for searching relevant passages: "` (not passages). Vector store wraps FAISS: build, atomic save, load with small in-process LRU cache, search returning `(chunk_id, score)`.

## 10. RAG pipeline (`ai/rag.py`, `ai/retriever.py`)

`RAGService.run_stream` steps:
1. Normalise query (strip pleasantries/boilerplate).
2. **Query expansion:** ask Gemini for 2 paraphrases (temp 0.2, ~200 tokens); cache in Redis `rag:qexp:{sha256[:32]}` for 1 h. Total 3 queries.
3. Embed all queries locally.
4. FAISS search per (query × document_id in session), top 20 each.
5. **Reciprocal Rank Fusion**, `score = Σ 1/(k + rank + 1)`, k=60; merge duplicate (doc, chunk), keep best FAISS score.
6. Hydrate chunk text + metadata from Postgres. Apply `RAG_SCORE_THRESHOLD`.
7. **Cross-encoder rerank** (`cross-encoder/ms-marco-MiniLM-L-6-v2`, lazy singleton) → top 8. Toggle with `RAG_ENABLE_RERANK`.
8. **MMR** (λ=0.5) → final 6 chunks (vectors already normalised, so similarity = inner product).
9. **Context assembly** within token budget: total 28,000 minus reserves (system 1,000, history 1,000, answer 1,500); pack chunks in order with tiktoken; each prefixed `[Source S{n} | doc={filename} | section={section_path} | pages={p1}-{p2}]`. History older than last 6 turns is collapsed into one synthetic summary turn.
10. **Select system prompt** by doc_type (below).
11. Stream Gemini; emit SSE events.
12. **Grounding check** (`ai/evaluation.py`): after generation compute a grounding score (overlap/similarity of answer sentences vs. source chunks). If `< RAG_GROUNDING_MIN_SCORE` set `finish_reason="grounding_low_confidence"` and surface a low-confidence warning to the client.
13. Persist the assistant message (content, sources, tokens, latency, model).

If retrieval returns nothing usable, emit `no_context` with the refusal phrase and **do not call the LLM**.

### System prompts (`ai/prompts.py`)
Refusal phrase constant: `"I cannot find this in the provided documents."`

Shared base rules (all prompts): (1) use ONLY the SOURCES section; (2) cite every factual claim with `[S1]`, multiple allowed `[S1][S3]`; (3) if insufficient evidence, reply with exactly the refusal phrase and stop; (4) never invent statutes, case names, docket numbers, dates, jurisdictions; (5) when sources conflict list each position with its citation; (6) answer in the user's language; (7) concise, structured.

Four domain suffixes: **CONTRACT** (organise by parties, term, obligations, payment, termination, liability/indemnity, governing law, risks; quote key clauses verbatim), **NDA** (definition of Confidential Information, permitted disclosures, term, return/destroy, residuals, governing law, remedies), **COURT_FILING** (court, caption, parties, claims, relief, procedural posture, dates, cited authorities; no extrapolation), **GENERAL** (one-sentence summary first, surface defined terms and cross-references). `select_prompt(doc_types, doc_type_hint=None, override=None)`: override (staff only) → hint → majority doc_type → general. `build_user_message(query, context)` formats `SOURCES:\n---\n{context}\n---\n\nQUESTION: {query}`.

### LLM client (`ai/llm.py`)
Gemini streaming in a worker thread bridged to asyncio via a bounded `asyncio.Queue`; generation config temp 0.2 / top_p 0.9 / max 1500 tokens; safety settings `BLOCK_NONE` for all categories (legal text trips filters); tenacity exponential backoff on ResourceExhausted/ServiceUnavailable/DeadlineExceeded/InternalServerError/429/timeouts; in-process **circuit breaker** (5 failures in 30 s → open 60 s); cancel upstream request on client disconnect (poll `request.is_disconnected()`).

### Chat routes (`routes/chat.py`)
- `POST /api/v1/chat/sessions` (title, document_ids — verify access to each), `GET` list, `GET /{id}` (with transcript), `PATCH`, `DELETE /{id}`
- `POST /api/v1/chat/sessions/{id}/messages` → `text/event-stream`
- `POST /api/v1/chat/preview` → same pipeline, nothing persisted
- Per-user Redis token bucket (20/min, burst 5) → 429 + `Retry-After`

**SSE events** (in order): `user_message {id}` → `sources {sources:[…]}` (before first token) → `token {text}`… → `done {text, finish_reason, usage, sources, latency_ms, model_name}` → `persisted {id, finish_reason}`; also `no_context {message}` and `error {message}`. `: heartbeat` comment every 15 s.

`Source` shape: `{chunk_id, document_id, filename, section_path, pages:[..], score, snippet(first 300 chars)}`; `[Sn]` markers align 1-to-1 with the array.

## 11. Django ⇄ FastAPI gateway

`apps/documents/services/fastapi_client.py` — a shared `httpx` client: forwards the incoming `Authorization: Bearer` header verbatim; 3 retries with 250 ms × 2ⁿ backoff on 5xx/network errors; error mapping: upstream 4xx → forwarded status+body, 5xx after retries → 502 (+`upstream_status`), connect failure → 503. All errors rendered as RFC-7807 `application/problem+json` by `common/exceptions.custom_exception_handler`.

| Django route | Forwards to |
|---|---|
| `POST /api/v1/documents/` | `POST /api/v1/documents/upload` (multipart) |
| `GET /api/v1/documents/` | local mirror only |
| `GET/DELETE /api/v1/documents/{id}/` | upstream + mirror sync (delete upstream-first) |
| `GET /api/v1/documents/{id}/status/` | passthrough |
| `WS /ws/documents/{id}/events/` | bridges FastAPI SSE `…/events` |
| `POST/GET /api/v1/chat/sessions/` | create upstream / list from mirror |
| `GET/DELETE /api/v1/chat/sessions/{id}/` | passthrough |
| `WS /ws/chat/{session_id}/?token=<JWT>` | bridges `POST …/messages` SSE |

**WebSocket consumers (Channels):** authenticate via JWT middleware; chat consumer joins group `chat.{session_id}` so multiple tabs get identical frames; stream upstream with `httpx.AsyncClient.stream`, parse SSE lines (`event:`, `data:`, blank-line delimiter, multi-line data joined with `\n`, ignore `:` comments); wrap each frame as `{"type": "<event>", "data": <payload>}`; reject a second user message while one is streaming; cancel the upstream stream on disconnect; on stream error send `{type:"error", detail}` then close. Redis channel layer via `channels-redis`.

Also expose OpenAPI docs at `/api/docs/` (drf-spectacular), Django admin at `/admin/`, health at `/healthz/`.

## 12. Web UI (`apps/web`) — server-rendered Django templates

Clean, modern, responsive CSS (single `app.css`, no JS framework; small vanilla JS where needed).

Routes: `login/`, `register/`, `logout/`, `profile/`, `profile/password/`, dashboard `/`, workspace switcher (stores current workspace in session; context processor exposes it), workspaces list/new/detail/members/invitations, `invitations/accept/<token>/`, documents list/upload/detail/delete, chat list/new/detail.

Key behaviours:
- Web session login uses the same JWT logic; the view obtains an access token for the logged-in user to call the gateway/FastAPI.
- Document detail page shows a status badge (partial template) and live ingestion progress via SSE (`documents/<id>/events` proxy view that streams from FastAPI).
- Chat detail page streams the answer via fetch + `ReadableStream` against `chat/<id>/stream` (Django view proxying FastAPI SSE). Render tokens incrementally with a Markdown renderer; replace `[Sn]` with anchored links/chips to the source list; show sources panel with filename, section, pages, snippet; show a "low confidence" notice when `finish_reason == grounding_low_confidence`. Disable send while streaming; handle `error`/`no_context`.
- Dashboard: counts of documents by status, recent documents, recent chats.
- Template tag `web_extras` for small filters (e.g. badge classes, file size).

## 13. Observability & ops

- `structlog` JSON logs on every stage transition (`document_id`, `stage`, `progress_pct`) and in RAG (`session_id`, `message_id`, `doc_ids`, `retrieved_chunk_ids`, scores).
- Prometheus at `/metrics` (FastAPI): `ldip_pipeline_failures_total{stage}`, `ldip_chunks_produced_total`, `ldip_documents_indexed_total`, histograms `ldip_ocr_seconds`, `ldip_chunk_seconds`, `ldip_embed_seconds`, `ldip_index_seconds`, `ldip_chunks_per_document`, `ldip_rag_retrieval_latency_seconds`, `ldip_rag_rerank_latency_seconds`, `ldip_llm_time_to_first_byte_seconds`, counters `ldip_llm_tokens_in_total`, `ldip_llm_tokens_out_total`, `ldip_rag_no_context_total`.
- `/health` (FastAPI: DB + Redis checks), `/healthz/` (Django).
- Sentry init if `SENTRY_DSN` set.
- CORS from `CORS_ORIGINS`; rate limit middleware on FastAPI (`RATE_LIMIT_PER_MINUTE`).

## 14. Tests (write alongside code)

FastAPI: `test_chunker`, `test_classifier`, `test_embedder`, `test_vectorstore`, `test_retriever` (RRF + MMR), `test_context_budget`, `test_prompts` (selector + refusal phrase), `test_jwt` (validity matrix: expired, wrong iss, missing claim, bad UUID, bad signature), `test_pipeline`, `test_rag_pipeline` (mocked Gemini stream), `test_rag_evaluation`, `test_gemini_smoke` (skipped without `GEMINI_API_KEY`).
Django: `test_auth_flow`, `test_workspace_permissions`, `test_documents_gateway` (mock FastAPI), `test_chat_ws` (Channels `WebsocketCommunicator`).

## 15. Build order (follow strictly; verify each phase)

1. **Scaffold & infra:** repo layout, `.env.example`, compose (postgres + redis), multi-DB init script, Dockerfiles, Makefile, health endpoints on both services. ✔ `docker compose up` → `/health` and `/healthz/` return 200.
2. **Auth & workspaces (Django):** user model, JWT with custom claims, auth endpoints, throttles, password validators, workspaces/members/invitations + permissions, tests. ✔ register → login → decode token and see `workspace_ids`.
3. **FastAPI foundation:** config, async DB, Alembic migrations, JWT verification dependency, rate limiter, metrics, file handler. ✔ unauthenticated request → 401; Django token → 200.
4. **Ingestion pipeline:** upload route, OCR engine, chunker, classifier, embedder, FAISS store, Celery chain, locks, events + SSE. ✔ upload a PDF, watch `ready`; FAISS files exist; re-upload same file is rejected.
5. **RAG & chat:** retriever (RRF/MMR/rerank), prompts, LLM client w/ retry + circuit breaker, evaluation/grounding, chat routes with SSE. ✔ `curl -N` a question, see `sources` then `token`s then `done`; an off-topic question yields the refusal phrase.
6. **Django gateway:** FastAPI client, documents/chat REST proxies + mirrors, WebSocket consumers. ✔ same flows work through Django on port 8000 incl. WS streaming.
7. **Web UI:** all templates/views listed in §12 with SSE progress and streaming chat with citations. ✔ full browser flow: register → upload → watch progress → chat → see cited sources.
8. **Hardening:** tests green, prod compose override, `.dockerignore`s, README with quickstart (`cp .env.example .env` → fill secrets → `make up` → `make migrate`), no secrets in git, `.gitignore` covers `.env`, `*.rdb`, `__pycache__`, data volumes.

## 16. Gotchas the original build hit (save yourself the time)

- Compose `command` for celery must include all three queues `ocr,embed,default`, and the worker must mount the same `faiss_data`/`media_data` volumes as FastAPI.
- First embedding/rerank call downloads models from HuggingFace; bake them into the image (or a cache volume) so workers don't stall/time out on first use.
- FAISS writes must be atomic (`.tmp` then `os.replace`) or a crash mid-write leaves a corrupt index.
- Gemini safety filters will block legal text unless set to `BLOCK_NONE`.
- Gemini SDK is synchronous for streaming — consume it in a thread and bridge to asyncio, or the event loop stalls.
- SSE through proxies: send heartbeats and `X-Accel-Buffering: no`; in the Django SSE proxy view use a streaming response and disable buffering.
- Chat UI streaming bugs to avoid: don't re-render the whole message on every token (append to a buffer node); render Markdown only on a throttled interval; make sure the final `done.text` replaces the streamed buffer; handle the case where `sources` arrives before any token.
- JWT `workspace_ids` is a snapshot — after accepting an invitation the user must refresh/re-login for the new workspace to appear in FastAPI authorization; surface that in the UI.
- Don't give the Gemini key (or any secret) a default value anywhere in code.
- Windows hosts: use Docker Desktop (WSL2 backend); keep line endings LF in shell scripts (`scripts/create_multiple_dbs.sh`) or the postgres init fails (`.gitattributes`: `*.sh text eol=lf`).
