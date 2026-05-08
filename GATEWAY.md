# GATEWAY.md — Django ⇄ FastAPI Gateway

Django acts as the public API surface. The browser **never** talks to FastAPI
directly. Django:

1. Authenticates the request (DRF + simplejwt).
2. Enforces workspace membership.
3. Forwards the user's **bearer token** unchanged to FastAPI.
4. Maps upstream errors to RFC-7807 problem responses.
5. Maintains a thin local mirror (workspace, owner, status) so listings and
   permission checks don't pay a cross-service round-trip.

Thin client lives at `apps/documents/services/fastapi_client.py` and is
shared by both the documents and chat apps.

## 1. Topology

```
browser ─JWT─▶ Django (DRF / Channels) ─JWT─▶ FastAPI
                  │                              │
                  ├── mirror tables              ├── auth source-of-truth
                  ├── permission checks          ├── ingestion / RAG / vectors
                  └── SSE→WS bridge              └── SSE progress / streaming
```

Compose service names: Django reaches FastAPI at `http://fastapi:8001`
(`FASTAPI_BASE_URL`). Default request timeout 120 s
(`FASTAPI_TIMEOUT_SECONDS`).

## 2. Documents

| Method | Django path                                  | Forwards to FastAPI                              |
|--------|----------------------------------------------|--------------------------------------------------|
| POST   | `/api/v1/documents/`                         | `POST /api/v1/documents/upload` (multipart)      |
| GET    | `/api/v1/documents/`                         | local mirror only                                |
| GET    | `/api/v1/documents/{id}/`                    | `GET /api/v1/documents/{id}` + refresh mirror    |
| DELETE | `/api/v1/documents/{id}/`                    | `DELETE /api/v1/documents/{id}` + delete mirror  |
| GET    | `/api/v1/documents/{id}/status/`             | `GET /api/v1/documents/{id}` (passthrough)       |
| WS     | `/ws/documents/{id}/events/`                 | bridges `GET /api/v1/documents/{id}/events` SSE  |

Lazy-pull keeps the mirror's `status`, `doc_type`, `page_count` aligned: each
GET attempts an upstream fetch and updates locally on diff. No background job
required.

## 3. Chat

| Method | Django path                              | Forwards to FastAPI                                |
|--------|------------------------------------------|----------------------------------------------------|
| POST   | `/api/v1/chat/sessions/`                 | `POST /api/v1/chat/sessions`                       |
| GET    | `/api/v1/chat/sessions/`                 | local mirror only                                  |
| GET    | `/api/v1/chat/sessions/{id}/`            | passthrough (full transcript from upstream)        |
| DELETE | `/api/v1/chat/sessions/{id}/`            | `DELETE /api/v1/chat/sessions/{id}` + local       |
| WS     | `/ws/chat/{session_id}/?token=<JWT>`     | bridges `POST /api/v1/chat/sessions/{id}/messages` |

The WebSocket consumer joins a Channels group `chat.{session_id}` so multiple
browser tabs viewing the same session receive identical token frames in
real-time.

## 4. SSE → WS bridge protocol

FastAPI emits these SSE event types over the chat message stream
(`app/routes/chat.py`):

| event           | purpose                                              |
|-----------------|------------------------------------------------------|
| `user_message`  | echo of the persisted user message id                |
| `token`         | one streamed token chunk                              |
| `sources`       | retrieved chunk citations                             |
| `done`          | finish_reason + usage                                 |
| `persisted`     | id of the persisted assistant message                 |
| `error`         | terminal error                                        |

For each frame the WebSocket consumer wraps the payload as:

```json
{ "type": "<event>", "data": <payload> }
```

Document SSE channel emits `snapshot`, `progress`, `end` — bridged
identically.

Heartbeats: FastAPI emits a `: heartbeat` SSE comment every 15 s. The bridge
ignores comments; clients use the `ready`/normal-message cadence to detect
dead sockets.

## 5. Error mapping

| Upstream condition                     | Django response                                |
|----------------------------------------|------------------------------------------------|
| 4xx with JSON body                     | Forwarded status + body, content-type problem  |
| 5xx after 3 retries (250ms × 2^n)      | `502 Bad Gateway` + `upstream_status`          |
| Network failure / connect timeout      | `503` after retry budget                       |
| Stream error mid-frame                 | WS frame `{type:"error", detail}` then close   |

All failure responses are emitted as `application/problem+json` by
`common.exceptions.custom_exception_handler`.

## 6. Streaming bridge implementation notes

- `httpx.AsyncClient.stream(...)` keeps the upstream connection alive.
- Each line of the SSE wire format is parsed (`event:`, `data:`, blank-line
  delimiter). Multi-line `data:` is rejoined with `\n`.
- On client disconnect the consumer cancels the stream task; the upstream
  connection is closed via the `async with` exit.
- The consumer rejects a second user message while a previous stream is in
  flight (`previous message still streaming`). Clients should wait for `done`
  or send `{"type": "ping"}` to verify liveness.

## 7. Mirror sync rules

| Field          | Source of truth | Updated on              |
|----------------|-----------------|-------------------------|
| `id`           | FastAPI         | upload response         |
| `status`       | FastAPI         | every GET, lazy-pull    |
| `doc_type`     | FastAPI         | every GET, lazy-pull    |
| `page_count`   | FastAPI         | every GET, lazy-pull    |
| `workspace`    | Django          | upload                  |
| `uploaded_by`  | Django          | upload                  |
| `title`        | Django          | upload                  |
| `sha256`       | FastAPI         | upload response         |

Deletion is **upstream-first**: Django calls `DELETE /api/v1/documents/{id}`
and only removes the local mirror on success or 404 (already gone). This
prevents orphaned vectors / files on FastAPI.

## 8. Bearer token forwarding

Django reads the request's `Authorization: Bearer <JWT>` header verbatim and
attaches it to every `httpx` call. The FastAPI service validates the token
locally — Django does **not** mint a separate service-to-service token. This
preserves end-user attribution in FastAPI logs (`user_id` claim).
