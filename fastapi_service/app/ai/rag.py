"""RAG orchestrator: query understanding → retrieval → context → LLM stream."""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import AsyncIterator, Iterable, List, Optional, Sequence

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.ai.llm import GeminiClient, StreamChunk
from app.ai.prompts import REFUSAL_PHRASE, build_user_message, select_prompt
from app.ai.retriever import RetrievedChunk, Retriever
from app.config import settings
from app.models.document import Chunk as ChunkRow, DocType, Document
from app.utils import metrics

logger = structlog.get_logger("rag")


# =========================================================================
# DTOs
# =========================================================================
@dataclass
class Source:
    chunk_id: str
    document_id: str
    filename: Optional[str]
    section_path: Optional[str]
    pages: Optional[List[int]]
    score: float
    snippet: str

    def to_dict(self) -> dict:
        return {
            "chunk_id": self.chunk_id,
            "document_id": self.document_id,
            "filename": self.filename,
            "section_path": self.section_path,
            "pages": self.pages,
            "score": round(self.score, 6),
            "snippet": self.snippet,
        }


@dataclass
class RAGRequest:
    query: str
    document_ids: List[uuid.UUID]
    chat_history: List[dict] = field(default_factory=list)
    system_prompt_override: Optional[str] = None
    doc_type_hint: Optional[DocType] = None


@dataclass
class RAGEvent:
    type: str  # "token" | "sources" | "done" | "error" | "no_context"
    data: dict

    def to_sse(self) -> bytes:
        payload = json.dumps(self.data, ensure_ascii=False)
        return f"event: {self.type}\ndata: {payload}\n\n".encode("utf-8")


# =========================================================================
# Helpers
# =========================================================================
# Strip leading greeting/pleasantry words (and an optional trailing comma),
# but only at the beginning of the query — never gobble the actual question.
_LEADING_GREETING_RE = re.compile(
    r"^(?:\s*(?:please|kindly|hi|hello|hey|dear)\b[ \t,]*)+",
    re.IGNORECASE,
)


def _normalise_query(q: str) -> str:
    q = (q or "").strip()
    q = _LEADING_GREETING_RE.sub("", q)
    q = re.sub(r"\s+", " ", q).strip()
    # Fall back to the original text if stripping somehow emptied it.
    return q


def _query_cache_key(query: str) -> str:
    h = hashlib.sha256(query.lower().encode("utf-8")).hexdigest()[:32]
    return f"rag:qexp:{h}"


def _format_source_marker(idx: int, chunk: RetrievedChunk) -> str:
    pages = ""
    if chunk.page_start is not None:
        pages = (
            f"{chunk.page_start}-{chunk.page_end}"
            if chunk.page_end and chunk.page_end != chunk.page_start
            else str(chunk.page_start)
        )
    section = chunk.section_path or "—"
    fname = chunk.filename or chunk.document_id
    return f"[Source S{idx} | doc={fname} | section={section} | pages={pages or '?'}]"


def _summarise_history(history: list[dict], max_turns: int) -> list[dict]:
    if len(history) <= max_turns:
        return history
    head = history[: len(history) - max_turns]
    tail = history[-max_turns:]
    summary_text = " | ".join(
        f"{t.get('role', 'user')}: {str(t.get('content', ''))[:200]}" for t in head
    )
    return [
        {"role": "user", "content": f"[Earlier conversation summary] {summary_text}"},
        *tail,
    ]


# =========================================================================
# RAG Service
# =========================================================================
class RAGService:
    def __init__(
        self,
        *,
        retriever: Optional[Retriever] = None,
        llm: Optional[GeminiClient] = None,
    ) -> None:
        self.retriever = retriever or Retriever()
        self.llm = llm or GeminiClient()

    # --------------------------------------------------------- run (stream)
    async def run_stream(
        self,
        request: RAGRequest,
        session: AsyncSession,
        *,
        is_staff: bool = False,
    ) -> AsyncIterator[RAGEvent]:
        t_start = time.monotonic()

        normalised = _normalise_query(request.query)
        if not normalised:
            # Defensive: if greeting-stripping consumed everything, fall
            # back to the raw query so we never reject a real user message.
            normalised = (request.query or "").strip()
        if not normalised:
            yield RAGEvent("error", {"message": "empty query"})
            return

        # --- query expansion -----------------------------------------------
        queries = [normalised]
        if settings.RAG_QUERY_EXPANSION:
            try:
                expansions = await self._expand_query(normalised)
                queries.extend(expansions)
            except Exception as exc:  # noqa: BLE001
                logger.warning("query_expansion_failed", error=str(exc))

        # --- retrieval -----------------------------------------------------
        docs = await self._load_documents(session, request.document_ids)
        if not docs:
            yield RAGEvent("error", {"message": "no documents available"})
            return

        retrieved = await asyncio.to_thread(
            self.retriever.retrieve,
            queries=queries,
            document_ids=[str(d.id) for d in docs],
        )
        if not retrieved:
            metrics.RAG_NO_CONTEXT.inc()
            yield RAGEvent("no_context", {"message": REFUSAL_PHRASE})
            yield RAGEvent("done", {"reason": "no_context"})
            return

        # Hydrate text + metadata from DB, then rerank+MMR.
        await self._hydrate_chunks(session, retrieved, docs)
        retrieved = [c for c in retrieved if c.text]
        if not retrieved:
            metrics.RAG_NO_CONTEXT.inc()
            yield RAGEvent("no_context", {"message": REFUSAL_PHRASE})
            yield RAGEvent("done", {"reason": "no_context"})
            return

        reranked = await asyncio.to_thread(
            self.retriever.rerank, normalised, retrieved
        )
        diversified = await asyncio.to_thread(
            self.retriever.mmr, normalised, reranked
        )

        # --- prompt --------------------------------------------------------
        sources, context_block = self._assemble_context(diversified, docs)
        if not sources:
            metrics.RAG_NO_CONTEXT.inc()
            yield RAGEvent("no_context", {"message": REFUSAL_PHRASE})
            yield RAGEvent("done", {"reason": "no_context"})
            return

        system_prompt = select_prompt(
            [d.doc_type for d in docs],
            doc_type_hint=request.doc_type_hint,
            override=request.system_prompt_override if is_staff else None,
        )
        user_message = build_user_message(normalised, context_block)
        history = _summarise_history(
            request.chat_history, settings.RAG_HISTORY_MAX_TURNS
        )

        # --- emit sources first so the UI can render anchors -------------
        yield RAGEvent("sources", {"sources": [s.to_dict() for s in sources]})

        # --- stream LLM ----------------------------------------------------
        first_byte_seen = False
        full_text_chunks: list[str] = []
        usage: dict = {}
        finish_reason: Optional[str] = None
        try:
            async for event in self.llm.stream(
                system_prompt=system_prompt,
                user_message=user_message,
                history=history,
            ):
                if event.text:
                    if not first_byte_seen:
                        first_byte_seen = True
                        metrics.LLM_TTFB.observe(time.monotonic() - t_start)
                    full_text_chunks.append(event.text)
                    yield RAGEvent("token", {"text": event.text})
                if event.finish_reason:
                    finish_reason = event.finish_reason
                if event.usage:
                    usage = event.usage
        except Exception as exc:  # noqa: BLE001
            logger.exception("llm_stream_failed", error=str(exc))
            yield RAGEvent("error", {"message": str(exc)})
            return

        full_text = "".join(full_text_chunks)
        if usage:
            metrics.LLM_TOKENS_IN.inc(usage.get("input_tokens", 0))
            metrics.LLM_TOKENS_OUT.inc(usage.get("output_tokens", 0))

        yield RAGEvent(
            "done",
            {
                "text": full_text,
                "finish_reason": finish_reason,
                "usage": usage,
                "sources": [s.to_dict() for s in sources],
                "latency_ms": int((time.monotonic() - t_start) * 1000),
                "model_name": self.llm.model_name,
            },
        )

    # --------------------------------------------------------- expansion
    async def _expand_query(self, query: str) -> list[str]:
        """Two paraphrases via Gemini Flash; cached in Redis for 1h."""
        try:
            from redis import asyncio as aioredis

            redis = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            cached = await redis.get(_query_cache_key(query))
            if cached:
                return json.loads(cached)
        except Exception:  # noqa: BLE001
            redis = None  # type: ignore[assignment]

        prompt = (
            "Rewrite the following legal question as TWO short paraphrases that "
            "preserve meaning but use different vocabulary. Output ONLY a JSON "
            'array of two strings, no explanation.\n\nQuestion: "' + query + '"'
        )
        try:
            result = await self.llm.generate(
                system_prompt="You produce concise paraphrases for retrieval.",
                user_message=prompt,
                temperature=0.2,
                max_output_tokens=200,
            )
            text = result.text.strip()
            start, end = text.find("["), text.rfind("]")
            if start >= 0 and end > start:
                paraphrases = json.loads(text[start : end + 1])
                paraphrases = [str(p)[:300] for p in paraphrases if str(p).strip()][:2]
            else:
                paraphrases = []
        except Exception as exc:  # noqa: BLE001
            logger.warning("paraphrase_failed", error=str(exc))
            paraphrases = []

        if redis is not None and paraphrases:
            try:
                await redis.set(
                    _query_cache_key(query),
                    json.dumps(paraphrases),
                    ex=settings.RAG_QUERY_CACHE_TTL,
                )
            except Exception:  # noqa: BLE001
                pass
        return paraphrases

    # --------------------------------------------------------- hydration
    async def _load_documents(
        self, session: AsyncSession, ids: Sequence[uuid.UUID]
    ) -> List[Document]:
        if not ids:
            return []
        res = await session.execute(select(Document).where(Document.id.in_(ids)))
        return list(res.scalars().all())

    async def _hydrate_chunks(
        self,
        session: AsyncSession,
        retrieved: List[RetrievedChunk],
        docs: List[Document],
    ) -> None:
        if not retrieved:
            return
        chunk_ids = list({c.chunk_id for c in retrieved})
        rows = (
            await session.execute(
                select(ChunkRow).where(ChunkRow.id.in_([uuid.UUID(c) for c in chunk_ids]))
            )
        ).scalars().all()
        by_id = {str(r.id): r for r in rows}
        doc_by_id = {str(d.id): d for d in docs}
        for c in retrieved:
            row = by_id.get(c.chunk_id)
            if row is None:
                continue
            c.text = row.text
            c.section_path = row.section_path
            c.page_start = row.page_start
            c.page_end = row.page_end
            doc = doc_by_id.get(c.document_id)
            if doc is not None:
                c.filename = doc.original_filename

    # --------------------------------------------------------- context
    def _assemble_context(
        self, chunks: List[RetrievedChunk], docs: List[Document]
    ) -> tuple[List[Source], str]:
        budget = (
            settings.RAG_TOTAL_TOKEN_BUDGET
            - settings.RAG_RESERVE_SYSTEM_TOKENS
            - settings.RAG_RESERVE_HISTORY_TOKENS
            - settings.RAG_RESERVE_ANSWER_TOKENS
        )
        from app.ai.chunker import _TokenCounter  # noqa: PLC2701

        counter = _TokenCounter()
        used = 0
        sources: list[Source] = []
        rendered: list[str] = []
        for i, c in enumerate(chunks, start=1):
            marker = _format_source_marker(i, c)
            block = f"{marker}\n{c.text.strip()}\n"
            cost = counter.count(block)
            if used + cost > budget:
                break
            used += cost
            rendered.append(block)
            pages = (
                [c.page_start] if c.page_start and not c.page_end
                else [c.page_start, c.page_end] if c.page_start and c.page_end
                else None
            )
            sources.append(
                Source(
                    chunk_id=c.chunk_id,
                    document_id=c.document_id,
                    filename=c.filename,
                    section_path=c.section_path,
                    pages=pages,
                    score=float(c.best_score),
                    snippet=(c.text or "")[:300],
                )
            )
        return sources, "\n".join(rendered)
