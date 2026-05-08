"""Unit tests for DocumentChunker."""
from __future__ import annotations

from app.ai.chunker import DocumentChunker
from app.ai.ocr_engine import OCRResult, PageResult


def _make_ocr(text: str) -> OCRResult:
    return OCRResult(
        full_text=text,
        pages=[PageResult(page_number=1, text=text, method="test")],
        page_spans=[(1, 0, len(text))],
    )


def test_chunker_produces_chunks(sample_text: str, doc_id: str) -> None:
    chunker = DocumentChunker(chunk_size=120, chunk_overlap=20, min_tokens=10)
    chunks = chunker.chunk(_make_ocr(sample_text), doc_id)
    assert len(chunks) > 0
    for c in chunks:
        assert c.text.strip() != ""
        assert c.token_count <= chunker.HARD_TOKEN_CAP
        assert c.hash and len(c.hash) == 64


def test_chunker_section_path_is_set(sample_text: str, doc_id: str) -> None:
    chunker = DocumentChunker(chunk_size=80, chunk_overlap=10, min_tokens=5)
    chunks = chunker.chunk(_make_ocr(sample_text), doc_id)
    paths = {c.section_path for c in chunks if c.section_path}
    assert any("Section 1" in p or "Section 2" in p or "Section 3" in p for p in paths)


def test_chunker_dedup_identical_text(doc_id: str) -> None:
    text = ("This is a unique sentence. " * 10) + ("This is a unique sentence. " * 10)
    chunker = DocumentChunker(chunk_size=50, chunk_overlap=0, min_tokens=2)
    chunks = chunker.chunk(_make_ocr(text), doc_id)
    hashes = [c.hash for c in chunks]
    assert len(hashes) == len(set(hashes))


def test_chunker_handles_empty_text(doc_id: str) -> None:
    assert DocumentChunker().chunk(_make_ocr(""), doc_id) == []
