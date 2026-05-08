# RAG Layer (Phase 3)

End-to-end retrieval-augmented generation over per-document FAISS indexes with
streaming Gemini answers, citations, and JWT-gated multi-document support.

## High-level flow

```
Client (Bearer JWT)
        │
        ▼
FastAPI /api/v1/chat/sessions/{id}/messages           (SSE)
        │
        ▼
RAGService.run_stream
        │
        │   1. normalise query (strip pleasantries / boilerplate)
        │   2. query expansion → 2 paraphrases via Gemini Flash (cached 1h)
        │   3. embed all 3 queries locally (BAAI/bge-small-en-v1.5)
        │   4. FAISS search per (query × document_id), top_k=20 each
        │   5. Reciprocal Rank Fusion (k=60) across all rankings
        │   6. hydrate text + metadata from Postgres
        │   7. cross-encoder rerank → top 8
        │   8. MMR (λ=0.5) → final 6 chunks
        │   9. assemble context within token budget
        │  10. select system prompt by doc_type
        │  11. stream Gemini 1.5 Flash → SSE token / sources / done events
        │  12. persist assistant message with sources, tokens, latency
        ▼
Client (token, sources, persisted)
```

## JWT auth (cross-service)

- Tokens are issued by Django (`rest_framework_simplejwt`) using the shared
  `JWT_SIGNING_KEY`. FastAPI verifies locally — no network call.
- Required claims: `iss="django-service"`, `exp`, `user_id` (UUID), `email`,
  `workspace_ids` (list[UUID]), `is_staff` (bool).
- Authorization rule: a user can read/query a document iff
  `document.owner_user_id == user_id` **OR**
  `document.workspace_id in user.workspace_ids` **OR** `is_staff`.
- All `/api/v1/documents/*` and `/api/v1/chat/*` routes are gated by
  `Depends(get_current_user)`.

## Rate limiting

- Per-user Redis token bucket (`app.security.rate_limit.consume`).
- Defaults: `CHAT_RATE_LIMIT_PER_MINUTE=20`, `CHAT_RATE_LIMIT_BURST=5`.
- Exceeded calls return `429` with `Retry-After`.

## Retrieval pipeline

### Query expansion

`Gemini Flash` is asked for 2 paraphrases (`temperature=0.2`, ~200 tokens) and
the result is cached in Redis under `rag:qexp:{sha256[:32]}` for 1h.

### FAISS search + RRF fusion

Each query vector searches each document's `IndexFlatIP` (or HNSW above 50k
vectors) for `RAG_PER_QUERY_TOP_K=20` hits. Results are fused with
**Reciprocal Rank Fusion**:

$$\text{RRF}(c) = \sum_{q \in Q} \frac{1}{k + \text{rank}_q(c) + 1},\ k=60$$

Duplicate `(document_id, chunk_id)` pairs are merged; the highest FAISS score
across queries is retained.

### Cross-encoder rerank

`cross-encoder/ms-marco-MiniLM-L-6-v2` (LOCAL, lazy-loaded singleton) scores
each `(query, chunk_text)` pair and re-sorts to **top 8**. Disabled by setting
`RAG_ENABLE_RERANK=false`.

### Maximal Marginal Relevance

We pick the final 6 chunks by maximising

$$\text{MMR}(c) = \lambda \cdot \text{sim}(q, c) - (1-\lambda) \cdot \max_{s \in S} \text{sim}(c, s)$$

with `λ=RAG_MMR_LAMBDA=0.5`. Vectors are produced by the same SBERT embedder
and are already L2-normalised, so similarity is just an inner product.

## Token budget

| Component         | Default tokens | Setting                         |
|-------------------|----------------|---------------------------------|
| Total prompt      | 28,000         | `RAG_TOTAL_TOKEN_BUDGET`        |
| System prompt     | reserve 1,000  | `RAG_RESERVE_SYSTEM_TOKENS`     |
| Chat history      | reserve 1,000  | `RAG_RESERVE_HISTORY_TOKENS`    |
| Answer (output)   | reserve 1,500  | `RAG_RESERVE_ANSWER_TOKENS`     |
| Context (sources) | rest (~24,500) | derived                         |

Chunks are packed in retrieval order using `tiktoken` (cl100k_base) and dropped
once the context reserve is exhausted. Each chunk is prefixed with:

```
[Source S{n} | doc={original_filename} | section={section_path} | pages={p1}-{p2}]
```

History longer than `RAG_HISTORY_MAX_TURNS=6` is summarised by collapsing the
older portion into a single synthetic turn.

## System prompts

`app/ai/prompts.py` defines four templates: `CONTRACT_PROMPT`, `NDA_PROMPT`,
`COURT_FILING_PROMPT`, `GENERAL_LEGAL_PROMPT`. Each instructs the model to:

- Answer ONLY from the SOURCES section.
- Cite every claim with `[S1]`, `[S2]`, ...; multiple markers allowed.
- Refuse with the exact phrase `"I cannot find this in the provided documents."`
  when evidence is insufficient.
- Never invent statutes, case names, or jurisdictions.
- Flag conflicts between sources.
- Match the user's query language.

Selection order: `system_prompt_override` (staff only) → `doc_type_hint` →
majority `doc_type` across the active documents. Falls back to
`GENERAL_LEGAL_PROMPT` for `unknown`.

## Streaming protocol (SSE)

`POST /api/v1/chat/sessions/{id}/messages` returns `text/event-stream` with:

| Event           | Payload                                                                     |
|-----------------|------------------------------------------------------------------------------|
| `user_message`  | `{ "id": "<uuid>" }`                                                         |
| `sources`       | `{ "sources": [Source, ...] }` emitted **before** the first token            |
| `token`         | `{ "text": "..." }` per Gemini chunk                                         |
| `no_context`    | `{ "message": "I cannot find this in the provided documents." }`             |
| `done`          | `{ "text", "finish_reason", "usage", "sources", "latency_ms", "model_name" }`|
| `error`         | `{ "message": "..." }`                                                       |
| `persisted`     | `{ "id": "<assistant_message_uuid>", "finish_reason": "stop" }`              |

A `: heartbeat` comment is emitted every 15s. Client disconnects cancel the
in-flight Gemini request via `request.is_disconnected()` polling and the
worker task's cancellation.

`POST /api/v1/chat/preview` runs the same pipeline but does NOT persist
anything (no session, no message rows).

## Citation format

The model is instructed to cite using `[Sn]` markers that align 1-to-1 with
the `sources` array emitted before the first token. Each `Source` carries:

```json
{
  "chunk_id": "uuid",
  "document_id": "uuid",
  "filename": "contract.pdf",
  "section_path": "Article 3 > Section 3.2",
  "pages": [4, 5],
  "score": 0.812,
  "snippet": "first 300 chars of the chunk..."
}
```

Renderers can replace `[Sn]` in the streamed text with anchored links to the
matching source.

## Generation

- Model: `gemini-1.5-flash` (configurable via `GEMINI_MODEL`).
- Generation config: `temperature=0.2`, `top_p=0.9`, `max_output_tokens=1500`.
- Safety settings: all four `HarmCategory` values set to `BLOCK_NONE` so legal
  language (statutes, case law, harassment claims) is not filtered.
- Streaming uses `generate_content(..., stream=True)` consumed in a worker
  thread and bridged to the asyncio event loop via a bounded `asyncio.Queue`.
- Tenacity exponential-backoff retry on `ResourceExhausted`,
  `ServiceUnavailable`, `DeadlineExceeded`, `InternalServerError`,
  `TooManyRequests` (and `Timeout`/`Connection` errors).
- A simple in-process **circuit breaker** (`app.ai.llm._CircuitBreaker`) opens
  after `LLM_CIRCUIT_FAIL_THRESHOLD=5` failures within
  `LLM_CIRCUIT_WINDOW_SECONDS=30`s and stays open for
  `LLM_CIRCUIT_OPEN_SECONDS=60`s.

## Persistence

Two new tables (alembic `003_phase3_chat`):

- `chat_sessions(id, user_id, workspace_id, title, document_ids JSONB, ...)`
- `chat_messages(id, session_id, role, content, sources JSONB, tokens_in,
  tokens_out, latency_ms, model_name, created_at)`

`document_ids` is stored as JSONB so a session can target multiple docs.

## Observability

Prometheus metrics (`/metrics`):

- `ldip_rag_retrieval_latency_seconds` — embed + FAISS + RRF
- `ldip_rag_rerank_latency_seconds`
- `ldip_llm_time_to_first_byte_seconds`
- `ldip_llm_tokens_in_total`, `ldip_llm_tokens_out_total`
- `ldip_rag_no_context_total`

Structlog records (`logger="rag"` / `logger="chat"`) include `session_id`,
`message_id`, `doc_ids`, `retrieved_chunk_ids`, and scores when relevant.

Sentry is initialised when `SENTRY_DSN` is set (5% trace sampling).

## Tuning knobs

| Setting                          | Default | What it controls                                    |
|----------------------------------|---------|-----------------------------------------------------|
| `RAG_PER_QUERY_TOP_K`            | 20      | FAISS hits returned per query per document          |
| `RAG_RRF_K`                      | 60      | RRF smoothing constant                              |
| `RAG_RERANK_TOP_K`               | 8       | Top chunks kept after cross-encoder rerank          |
| `RAG_FINAL_K`                    | 6       | Chunks sent to the LLM after MMR                    |
| `RAG_MMR_LAMBDA`                 | 0.5     | Relevance vs diversity in MMR                       |
| `RAG_TOTAL_TOKEN_BUDGET`         | 28000   | Soft total prompt cap                               |
| `RAG_HISTORY_MAX_TURNS`          | 6       | Tail of chat kept verbatim; older summarised        |
| `RAG_QUERY_EXPANSION`            | true    | Enable Gemini-based 2× paraphrase expansion         |
| `RAG_QUERY_CACHE_TTL`            | 3600    | Redis TTL for paraphrase cache                      |
| `RAG_ENABLE_RERANK`              | true    | Enable cross-encoder rerank                         |
| `RERANKER_MODEL_NAME`            | ms-marco-MiniLM-L-6-v2 | Cross-encoder model                  |
| `LLM_TEMPERATURE`                | 0.2     | Gemini temperature                                  |
| `LLM_MAX_OUTPUT_TOKENS`          | 1500    | Hard cap on generated tokens                        |
| `LLM_CIRCUIT_FAIL_THRESHOLD`     | 5       | Failures within window before tripping              |
| `CHAT_RATE_LIMIT_PER_MINUTE`     | 20      | Per-user chat-message rate                          |

## Acceptance checks

- `pytest fastapi_service/tests/test_jwt.py` — JWT validation matrix.
- `pytest fastapi_service/tests/test_prompts.py` — selector + refusal phrase.
- `pytest fastapi_service/tests/test_retriever.py` — RRF and MMR.
- `pytest fastapi_service/tests/test_context_budget.py` — packing logic.
- `pytest fastapi_service/tests/test_rag_pipeline.py` — orchestrator with a
  mocked Gemini stream.
- `GEMINI_API_KEY=... pytest fastapi_service/tests/test_gemini_smoke.py` —
  one-shot real-API smoke.
