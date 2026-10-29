"""Unit tests for RRF and MMR."""
from __future__ import annotations

import numpy as np

from app.ai.retriever import RetrievedChunk, Retriever, _mmr_select, _reciprocal_rank_fusion
from app.ai.vectorstore import SearchHit


def _hit(doc: str, chunk: str, score: float) -> SearchHit:
    return SearchHit(document_id=doc, chunk_id=chunk, score=score)


def test_rrf_aggregates_across_queries() -> None:
    r1 = [_hit("d", "c1", 0.9), _hit("d", "c2", 0.8), _hit("d", "c3", 0.7)]
    r2 = [_hit("d", "c2", 0.95), _hit("d", "c4", 0.6)]
    fused = _reciprocal_rank_fusion([r1, r2], k=60)
    chunk_ids = [c.chunk_id for c in fused]
    # c2 appears top in r2 and 2nd in r1 → should rank highest.
    assert chunk_ids[0] == "c2"
    assert set(chunk_ids) == {"c1", "c2", "c3", "c4"}


def test_rrf_handles_empty() -> None:
    assert _reciprocal_rank_fusion([], k=60) == []
    assert _reciprocal_rank_fusion([[]], k=60) == []


def test_mmr_picks_diverse_chunks() -> None:
    rng = np.random.default_rng(0)

    def _norm(v: np.ndarray) -> np.ndarray:
        return v / np.linalg.norm(v)

    q = _norm(np.array([1.0, 0.0, 0.0]))
    a = _norm(np.array([0.99, 0.01, 0.0]))
    a_dup = _norm(a + rng.normal(0, 0.001, 3))
    b = _norm(np.array([0.6, 0.8, 0.0]))
    c = _norm(np.array([0.5, 0.0, 0.85]))

    chunks = [
        RetrievedChunk(document_id="d", chunk_id=f"c{i}", score=0.0, embedding=v)
        for i, v in enumerate([a, a_dup, b, c])
    ]
    selected = _mmr_select(q, chunks, final_k=3, lambda_mult=0.1)
    ids = [c.chunk_id for c in selected]
    expected_first = "c0" if float(q @ a) >= float(q @ a_dup) else "c1"
    duplicate = "c1" if expected_first == "c0" else "c0"
    assert ids[0] == expected_first
    assert duplicate not in ids  # the near-duplicate is excluded for diversity
    assert len(set(ids)) == 3


def test_retriever_mmr_reuses_vectors_from_store(monkeypatch) -> None:
    vectors = {
        ("d", "c0"): np.array([1.0, 0.0], dtype=np.float32),
        ("d", "c1"): np.array([0.0, 1.0], dtype=np.float32),
        ("d", "c2"): np.array([-1.0, 0.0], dtype=np.float32),
    }

    class _Embedder:
        document_texts = None

        def embed_documents(self, texts):
            self.document_texts = texts
            return np.zeros((len(texts), 2), dtype=np.float32)

        def embed_query(self, query):
            return np.array([1.0, 0.0], dtype=np.float32)

    class _VectorStore:
        def get_vectors(self, chunk_refs):
            return {ref: vectors[ref] for ref in chunk_refs if ref in vectors}

    from app.config import settings

    monkeypatch.setattr(settings, "RAG_FINAL_K", 2)
    embedder = _Embedder()
    retriever = Retriever(embedder=embedder, vector_store=_VectorStore())
    chunks = [
        RetrievedChunk(document_id="d", chunk_id=f"c{i}", score=1.0, text=f"text {i}")
        for i in range(3)
    ]

    selected = retriever.mmr("query", chunks)

    assert len(selected) == 2
    assert embedder.document_texts is None
    assert all(chunk.embedding is not None for chunk in chunks)


def test_retriever_mmr_embeds_only_vectors_missing_from_store(monkeypatch) -> None:
    class _Embedder:
        document_texts = None

        def embed_documents(self, texts):
            self.document_texts = texts
            return np.array([[0.0, 1.0]], dtype=np.float32)

        def embed_query(self, query):
            return np.array([1.0, 0.0], dtype=np.float32)

    class _VectorStore:
        def get_vectors(self, chunk_refs):
            return {("d", "c0"): np.array([1.0, 0.0], dtype=np.float32)}

    from app.config import settings

    monkeypatch.setattr(settings, "RAG_FINAL_K", 1)
    embedder = _Embedder()
    retriever = Retriever(embedder=embedder, vector_store=_VectorStore())
    chunks = [
        RetrievedChunk(document_id="d", chunk_id="c0", score=1.0, text="indexed"),
        RetrievedChunk(document_id="d", chunk_id="c1", score=0.5, text="fallback"),
    ]

    retriever.mmr("query", chunks)

    assert embedder.document_texts == ["fallback"]
    np.testing.assert_array_equal(chunks[0].embedding, [1.0, 0.0])
    np.testing.assert_array_equal(chunks[1].embedding, [0.0, 1.0])


def test_retriever_mmr_falls_back_for_legacy_vector_store(monkeypatch) -> None:
    class _Embedder:
        def embed_documents(self, texts):
            return np.array([[0.0, 1.0], [1.0, 0.0]], dtype=np.float32)

        def embed_query(self, query):
            return np.array([1.0, 0.0], dtype=np.float32)

    from app.config import settings

    monkeypatch.setattr(settings, "RAG_FINAL_K", 1)
    retriever = Retriever(embedder=_Embedder(), vector_store=object())
    chunks = [
        RetrievedChunk(document_id="d", chunk_id="c0", score=1.0, text="fallback"),
        RetrievedChunk(document_id="d", chunk_id="c1", score=0.5, text="also fallback"),
    ]

    selected = retriever.mmr("query", chunks)

    assert len(selected) == 1
    np.testing.assert_array_equal(chunks[0].embedding, [0.0, 1.0])
