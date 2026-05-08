"""Token-budget assembly tests."""
from __future__ import annotations

import uuid

from app.ai.rag import RAGService
from app.ai.retriever import RetrievedChunk
from app.config import settings


def _chunk(text: str, ordinal: int = 0) -> RetrievedChunk:
    return RetrievedChunk(
        document_id=str(uuid.uuid4()),
        chunk_id=str(uuid.uuid4()),
        score=1.0 - ordinal * 0.01,
        rank=ordinal,
        text=text,
        section_path="Section 1",
        page_start=1,
        page_end=1,
        filename="doc.pdf",
    )


def test_context_packs_until_budget() -> None:
    svc = RAGService.__new__(RAGService)
    chunks = [_chunk("word " * 200, i) for i in range(50)]
    sources, ctx = svc._assemble_context(chunks, docs=[])  # type: ignore[arg-type]
    assert sources, "should pack at least one chunk"
    assert "Source S1" in ctx
    # Should have stopped before consuming all 50 chunks under the budget.
    expected_max = (
        settings.RAG_TOTAL_TOKEN_BUDGET
        - settings.RAG_RESERVE_SYSTEM_TOKENS
        - settings.RAG_RESERVE_HISTORY_TOKENS
        - settings.RAG_RESERVE_ANSWER_TOKENS
    )
    # Rough char/4 token estimate: each chunk ~200 tokens.
    assert len(sources) <= max(1, expected_max // 200) + 5


def test_context_marker_includes_metadata() -> None:
    svc = RAGService.__new__(RAGService)
    chunks = [_chunk("hello world", 0)]
    _, ctx = svc._assemble_context(chunks, docs=[])  # type: ignore[arg-type]
    assert "doc=doc.pdf" in ctx
    assert "section=Section 1" in ctx
    assert "pages=1" in ctx
