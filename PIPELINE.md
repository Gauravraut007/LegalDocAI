# Document Ingestion Pipeline (Phase 2)

A document upload triggers a 5-stage Celery chain. Every stage is **idempotent**, **resumable**, **observable** (Prometheus + Redis pub/sub events), and serialised per document via a Postgres advisory lock.

```
upload (HTTP) ──► validate ──► ocr ──► chunk ──► embed ──► index ──► ready
                              (queue: ocr)        (queue: embed)
```

## Stages

| # | Task | Queue | Input | Output | Side effects | Retry policy | Idempotency rule |
|---|---|---|---|---|---|---|---|
| 1 | `validate_document` | `default` | `document_id` | `document_id` | DB row updated to `validating`; advisory lock acquired | `IOError`/`OSError`/`Connection`/`Timeout`, exp backoff, `max_retries=3` | Skips if `Document.status == ready` |
| 2 | `ocr_document` | `ocr` | `document_id` | `document_id` | Writes `ocr.txt` + `ocr_spans.json`; `DocumentVersion(kind=ocr_text)` upserted; sets `page_count`, `language`, `doc_type` | exp backoff, `max_retries=2` | Skips if `Document.status == ready`; `DocumentVersion.upsert` |
| 3 | `chunk_document` | `default` | `document_id` | `document_id` | Inserts rows into `chunks` (deletes prior rows first) | exp backoff, `max_retries=2` | Re-runs deterministically (delete+insert) |
| 4 | `embed_document` | `embed` | `document_id` | `document_id` | Writes FAISS index (atomic `.tmp` → `replace`) + `embeddings_meta` row | exp backoff, `max_retries=3` | Skips if `EmbeddingsMeta.vector_count == len(chunks)` for the current model |
| 5 | `index_document` | `default` | `document_id` | `document_id` | Sets `Document.status = ready`, `IngestJob.state = succeeded` | exp backoff, `max_retries=2` | Verifies FAISS file exists, then flips status |

On any failure: stage marks `Document.status = failed`, writes `error_message`, sets `IngestJob.state = failed`, increments `ldip_pipeline_failures_total{stage=...}`, publishes a `failed` event.

## Concurrency control

Per-document Postgres advisory lock via `pg_try_advisory_lock(hashtext(uuid))` (`app.utils.locks.document_lock`). Same document never runs in parallel across workers.

## Redis event schema

Each stage publishes JSON to channel `ingest:{document_id}` and stores the latest snapshot at key `ingest:{document_id}:latest` (TTL 1h).

```json
{
  "stage": "ocr",
  "progress_pct": 35,
  "message": "OCR complete",
  "ts": "2024-01-01T12:00:00.000Z",
  "extra": {"pages": 4, "language": "en"}
}
```

Terminal stages: `ready`, `failed`. Clients should close the SSE stream when either is observed.

## Live progress (SSE)

`GET /api/v1/documents/{id}/events`

The endpoint:
1. Replays the latest snapshot if present.
2. Subscribes to the pub/sub channel.
3. Emits `event: progress` frames as new events arrive; `event: end` when terminal.
4. Sends `: keep-alive` comments every 15s to defeat proxy buffering.

## Storage layout

```
/data/uploads/{owner_id}/{document_id}/
    original.{ext}        # streamed during upload, sha256 computed in flight
    ocr.txt               # full OCR text (atomic write)
    ocr_spans.json        # per-page char span map + metadata
/data/faiss/
    {document_id}.index       # IndexFlatIP, or HNSW above 50k vectors
    {document_id}.meta.json   # {chunk_ids, model_name, dim, type, count}
```

## Observability

Prometheus metrics (`/metrics`):

- `ldip_pipeline_failures_total{stage}` — counter
- `ldip_chunks_produced_total` — counter
- `ldip_documents_indexed_total` — counter
- `ldip_ocr_seconds`, `ldip_chunk_seconds`, `ldip_embed_seconds`, `ldip_index_seconds` — histograms
- `ldip_chunks_per_document` — histogram

Structlog JSON logs are emitted on every stage transition with `document_id`, `stage`, `progress_pct`.

## Debugging a stuck document

```sh
# 1. Inspect DB state
psql $DATABASE_URL -c "SELECT id, status, error_message, updated_at FROM documents WHERE id = '<uuid>';"
psql $DATABASE_URL -c "SELECT * FROM ingest_jobs WHERE document_id = '<uuid>' ORDER BY created_at DESC LIMIT 5;"

# 2. Tail live events
redis-cli -u $REDIS_URL SUBSCRIBE ingest:<uuid>
redis-cli -u $REDIS_URL GET ingest:<uuid>:latest

# 3. Inspect Celery
docker compose exec celery_worker celery -A app.workers.tasks inspect active
docker compose exec celery_worker celery -A app.workers.tasks inspect reserved
docker compose exec celery_worker celery -A app.workers.tasks inspect stats

# 4. Inspect FAISS index
docker compose exec fastapi ls -lah /data/faiss | grep <uuid>
docker compose exec fastapi cat /data/faiss/<uuid>.meta.json | jq .
```

## Re-queueing a failed document

```python
from app.workers.tasks import launch_pipeline
launch_pipeline("<document_id>")
```

All stages are idempotent: completed work is detected and skipped.

## Local development

```sh
make up                          # postgres + redis + fastapi + celery_worker + celery_beat + django
make migrate                     # alembic upgrade head
make logs                        # tail compose logs
docker compose exec fastapi pytest -q tests/
```
