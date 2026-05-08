"""End-to-end RAG test with a mocked Gemini stream.

We monkey-patch :class:`app.ai.rag.RAGService` to use a fake LLM and a fake
retriever, then call ``run_stream`` directly. This avoids requiring a live
Postgres / FAISS / network — it validates the orchestrator wiring.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import AsyncIterator

import pytest

from app.ai.llm import StreamChunk
from app.ai.rag import RAGRequest, RAGService
from app.ai.retriever import RetrievedChunk
from app.models.document import DocStatus, DocType, Document


class _FakeLLM:
    model_name = "fake-flash"

    async def generate(self, **kwargs):  # noqa: ANN003 - test stub
        from app.ai.llm import CompletionResult

        return CompletionResult(text='["paraphrase one", "paraphrase two"]', usage={})

    async def stream(self, **kwargs) -> AsyncIterator[StreamChunk]:  # noqa: ANN003
        for tok in ["The ", "agreement ", "is between ", "Acme and Foo. [S1]"]:
            yield StreamChunk(text=tok)
        yield StreamChunk(
            finish_reason="stop",
            usage={"input_tokens": 100, "output_tokens": 12, "total_tokens": 112},
        )


class _FakeRetriever:
    def __init__(self, chunks):
        self._chunks = chunks

    def retrieve(self, **kwargs):  # noqa: ANN003
        return list(self._chunks)

    def rerank(self, query, chunks, **kwargs):  # noqa: ANN001, ANN003
        return chunks

    def mmr(self, query, chunks, **kwargs):  # noqa: ANN001, ANN003
        return chunks


class _FakeAsyncSession:
    """Minimal stand-in for AsyncSession returning canned documents/chunks."""

    def __init__(self, docs, chunk_rows) -> None:
        self._docs = {d.id: d for d in docs}
        self._chunks = {c.id: c for c in chunk_rows}

    async def execute(self, stmt):
        sql = str(stmt).lower()
        if "from documents" in sql:
            return _FakeResult(list(self._docs.values()))
        if "from chunks" in sql:
            return _FakeResult(list(self._chunks.values()))
        return _FakeResult([])


class _FakeResult:
    def __init__(self, items):
        self._items = items

    def scalars(self):
        return self

    def all(self):
        return self._items


class _ChunkRow:
    def __init__(self, *, id_, doc_id, text, section, page):
        self.id = id_
        self.document_id = doc_id
        self.text = text
        self.section_path = section
        self.page_start = page
        self.page_end = page


@pytest.mark.asyncio
async def test_rag_streams_tokens_and_sources() -> None:
    doc_id = uuid.uuid4()
    chunk_id = uuid.uuid4()
    other_chunk_id = uuid.uuid4()
    doc = Document(
        id=doc_id,
        owner_user_id=uuid.uuid4(),
        workspace_id=None,
        original_filename="contract.pdf",
        stored_path="/tmp/x",
        mime_type="application/pdf",
        size_bytes=1,
        sha256="x",
        doc_type=DocType.contract,
        status=DocStatus.ready,
    )
    rows = [
        _ChunkRow(
            id_=chunk_id,
            doc_id=doc_id,
            text="The agreement is between Acme and Foo and governs confidentiality.",
            section="Section 1",
            page=1,
        ),
        _ChunkRow(
            id_=other_chunk_id,
            doc_id=doc_id,
            text="The term of the contract is two years and may be extended.",
            section="Section 3",
            page=2,
        ),
    ]
    fake_chunks = [
        RetrievedChunk(
            document_id=str(doc_id),
            chunk_id=str(chunk_id),
            score=0.9,
        ),
        RetrievedChunk(
            document_id=str(doc_id),
            chunk_id=str(other_chunk_id),
            score=0.5,
        ),
    ]

    svc = RAGService(retriever=_FakeRetriever(fake_chunks), llm=_FakeLLM())  # type: ignore[arg-type]
    # Disable query expansion for this synthetic test.
    from app.config import settings

    original_qe = settings.RAG_QUERY_EXPANSION
    settings.RAG_QUERY_EXPANSION = False
    try:
        events = []
        async for ev in svc.run_stream(
            RAGRequest(query="who are the parties?", document_ids=[doc_id]),
            session=_FakeAsyncSession([doc], rows),  # type: ignore[arg-type]
        ):
            events.append(ev)
    finally:
        settings.RAG_QUERY_EXPANSION = original_qe

    types = [e.type for e in events]
    assert "sources" in types
    assert types.count("token") >= 4
    assert types[-1] == "done"
    sources_event = next(e for e in events if e.type == "sources")
    assert sources_event.data["sources"]
    done = events[-1]
    assert done.data["finish_reason"] == "stop"
    assert "[S1]" in done.data["text"]
