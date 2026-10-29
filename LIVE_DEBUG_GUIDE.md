# Live Debug Guide — watch your own pipeline run

Your resume-numbers / resume-vs-reality report is in **[INTERVIEW_PREP.md](INTERVIEW_PREP.md)**
(same folder as this file). This file is the companion: exact commands to run, exactly
where to look, so you can *watch* the pipeline execute instead of reading about it.

Good news: the codebase already logs almost everything you need — structured JSON logs
at every stage, plus Prometheus timing histograms. You don't need to add prints to see
the pipeline work; you need to know where to look. I'll also show you where to add a
print/log line for the few spots that are currently silent.

---

## 1. Start everything and watch logs live

Open **4 terminals** side by side (this matters — one feed per thing you care about).

```bash
# Terminal 1: start the whole stack in the foreground (NOT -d), so logs stream
docker compose up
```

If you prefer it detached with separate log tails instead:

```bash
docker compose up -d
```

```bash
# Terminal 2: FastAPI logs — this is your QUERY-TIME flow (chat/RAG)
docker compose logs -f fastapi
```

```bash
# Terminal 3: Celery worker logs — this is your INGESTION flow (upload → chunk → embed → index)
docker compose logs -f celery_worker
```

```bash
# Terminal 4: free to use for curl commands / uploading test documents
```

Optional 5th terminal — Celery's web UI, shows task history/timing visually:

```bash
docker compose --profile debug up -d flower
# then open http://localhost:5555
```

---

## 2. Watch document ingestion (upload → searchable)

In terminal 4, upload a real PDF (replace the token/path):

```bash
curl -X POST http://localhost:8000/api/v1/documents/upload \
  -H "Authorization: Bearer <your-jwt>" \
  -F "file=@C:/path/to/test.pdf"
```

### What to watch for in Terminal 3 (celery_worker)

Because the worker runs with `-l info` (already set in
[docker-compose.yml:84](docker-compose.yml#L84)), **Celery itself automatically prints
every task's start and runtime** — you don't need to add anything to see this part:

```
[INFO] Task app.workers.tasks.validate_document[...] received
[INFO] Task app.workers.tasks.validate_document[...] succeeded in 0.0421s
[INFO] Task app.workers.tasks.ocr_document[...] received
[INFO] Task app.workers.tasks.ocr_document[...] succeeded in 3.812s
[INFO] Task app.workers.tasks.chunk_document[...] received
[INFO] Task app.workers.tasks.chunk_document[...] succeeded in 0.263s
[INFO] Task app.workers.tasks.embed_document[...] received
[INFO] Task app.workers.tasks.embed_document[...] succeeded in 1.904s
[INFO] Task app.workers.tasks.index_document[...] received
[INFO] Task app.workers.tasks.index_document[...] succeeded in 0.088s
```

**This is your real "how long does each stage take" answer** — stop guessing the
resume's "58% faster" claim and just read these numbers off a real run. OCR will
dominate for scanned PDFs; embedding dominates for huge documents.

### Where the timing actually comes from internally (so you can explain it)

Each stage wraps itself in `time.monotonic()` and records a Prometheus histogram —
[fastapi_service/app/workers/tasks.py](fastapi_service/app/workers/tasks.py):
- line 422 → `metrics.OCR_DURATION.observe(...)`
- line 549 → `metrics.CHUNK_DURATION.observe(...)`
- line 708 → `metrics.EMBEDDING_DURATION.observe(...)`
- line 772 → `metrics.INDEX_DURATION.observe(...)`

These are currently **metrics-only, not printed to the log line itself**. If you want
the duration visible directly in the log stream (nice for a demo/interview), add one
line right after each `.observe(...)` call, e.g. after line 422:

```python
metrics.OCR_DURATION.observe(time.monotonic() - started)
slog.info("stage_timing", stage="ocr", seconds=round(time.monotonic() - started, 3))
```

Do the same pattern after lines 549, 708, 772 (`stage="chunk"`, `"embed"`, `"index"`).
Now `docker compose logs -f celery_worker` will show a clean `stage_timing` JSON line
per stage, independent of Celery's own formatting.

### Watch the chunking happen concretely

Add a temporary print inside `_pack_section` in
[fastapi_service/app/ai/chunker.py](fastapi_service/app/ai/chunker.py) (around line 311-377)
to see each chunk as it's created:

```python
print(f"[CHUNK] tokens={count} section={section_path!r} preview={text[:60]!r}")
```

Run the upload again and you'll see every chunk boundary decision live in Terminal 3.
**Remove this print before committing** — it's a debug-only line.

### Watch progress events (what the frontend sees)

```bash
curl -N http://localhost:8000/api/v1/documents/<document_id>/events \
  -H "Authorization: Bearer <your-jwt>"
```

`-N` disables curl's buffering so you see each SSE event as it's published — this is
literally the same stream the browser's progress bar consumes
([documents.py:346](fastapi_service/app/routes/documents.py#L346)).

---

## 3. Watch a chat query (the RAG flow)

Send a message via REST directly (bypasses the WebSocket/Django layer, hits FastAPI's
RAG pipeline directly — simplest way to see it in isolation):

```bash
curl -N -X POST http://localhost:8000/api/v1/chat/preview \
  -H "Authorization: Bearer <your-jwt>" \
  -H "Content-Type: application/json" \
  -d '{"document_ids": ["<document_id>"], "query": "What is the termination clause?"}'
```

### What to watch for in Terminal 2 (fastapi)

[fastapi_service/app/ai/rag.py](fastapi_service/app/ai/rag.py) already logs a
structured event at every stage boundary — you'll see these JSON lines fire in order,
each with its own timestamp so you can eyeball the gap between them:

```
rag_started            query=... document_count=1
rag_query_expansion_started
rag_query_expansion_finished   expansion_count=2
rag_retrieval_started          query_count=3
rag_retrieval_finished         result_count=20
rag_rerank_started              chunk_count=20
rag_rerank_finished              result_count=8
rag_sources_ready               source_count=6
rag_llm_started                 model=gemini-2.5-flash
rag_llm_first_token              latency_ms=842
```

Since every line is a separate structlog call with its own implicit timestamp (added
by `structlog`/`logging`), **the gap between consecutive timestamps is your real
per-stage latency** — e.g. time between `rag_retrieval_started` and
`rag_retrieval_finished` is actual FAISS+RRF time; between `rag_rerank_started` and
`_finished` is actual cross-encoder time. This is exactly how you'd get a real,
honest number to replace the resume's unverified "200-500ms."

If you want explicit elapsed-ms printed per stage instead of eyeballing timestamp gaps,
wrap each block, e.g. around
[rag.py:173-179](fastapi_service/app/ai/rag.py#L173-L179):

```python
t0 = time.monotonic()
logger.info("rag_retrieval_started", query_count=len(queries))
retrieved = ...  # existing retrieval call
logger.info("rag_retrieval_finished", result_count=len(retrieved),
            elapsed_ms=int((time.monotonic() - t0) * 1000))
```

Repeat the same `t0 = time.monotonic()` / `elapsed_ms=...` pattern around the rerank
block (~line 195-197) and the MMR step if you want it isolated too (MMR currently has
no log line at all — add one in `retriever.py` around the `_mmr_select` call).

### Watch retrieval/rerank/MMR internals directly

[fastapi_service/app/ai/retriever.py](fastapi_service/app/ai/retriever.py) has no
print/log statements inside `_reciprocal_rank_fusion` (~line 170) or `_mmr_select`
(~line 199) — add temporary prints to see the actual chunk IDs and scores at each step:

```python
# inside _reciprocal_rank_fusion, right before returning
for c in fused[:5]:
    print(f"[RRF] doc={c['document_id']} chunk={c['chunk_id']} score={c['score']:.4f}")
```

```python
# inside _mmr_select, inside the greedy loop, after picking the next chunk
print(f"[MMR] picked chunk={chosen['chunk_id']} relevance={rel:.3f} redundancy_penalty={red:.3f}")
```

This is the single best exercise for actually understanding MMR/RRF instead of reading
the formula — watch the scores change as the algorithm runs on a real query.

---

## 4. See the real timing numbers, no guessing

Once you've sent a handful of chat messages and uploaded a couple of documents, pull
the actual Prometheus metrics FastAPI has been recording the whole time:

```bash
curl http://localhost:8000/metrics | grep ldip_
```

You'll see real histograms, e.g.:

```
ldip_rag_retrieval_latency_seconds_bucket{le="0.1"} ...
ldip_rag_retrieval_latency_seconds_sum ...
ldip_rag_retrieval_latency_seconds_count ...
ldip_llm_time_to_first_byte_seconds_sum ...
ldip_ocr_duration_seconds_sum ...
ldip_embedding_duration_seconds_sum ...
```

`sum / count` = your real average latency for that stage. Do this after ~10-20 queries
and you have an honest, defensible number to say in an interview instead of the
resume's invented "200-500ms" / "<1.8s" — and it's *better* than the resume number
because you can explain exactly how it was measured.

---

## 5. Cleanup reminder

Every `print(...)` added above is a temporary debug aid — grep for `print(` in
`chunker.py`, `retriever.py` before you're done and remove them; the project uses
`structlog`/`logger` everywhere else on purpose (structured, leveled, JSON-parseable),
not raw prints.

---

## Where things are (quick index)

| Want to see... | File | Already logs? |
|---|---|---|
| Upload → dedup → chain launch | `app/routes/documents.py:88` | yes (`slog.info` at tasks.py:110) |
| OCR extraction | `app/ai/ocr_engine.py` | timing only (metrics), add prints for per-page detail |
| Chunking | `app/ai/chunker.py:84-377` | no logs — add prints (§2) |
| Embedding | `app/ai/embedder.py` | timing only (metrics) |
| FAISS build/search | `app/ai/vectorstore.py` | timing only (metrics) |
| Query expansion | `app/ai/rag.py:159-165` | yes, structured logs |
| RRF fusion | `app/ai/retriever.py:170-193` | no logs — add prints (§3) |
| Cross-encoder rerank | `app/ai/rag.py:195-197` | yes, start/finish only (no scores) |
| MMR selection | `app/ai/retriever.py:199-228` | no logs at all — add prints (§3) |
| Gemini streaming + first token | `app/ai/rag.py:219-234` | yes, includes TTFB latency |
| Grounding gate (answer discarded?) | `app/ai/rag.py:251-271` | yes, `logger.warning` |
