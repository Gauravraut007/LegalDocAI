"""Unit tests for RRF and MMR."""
from __future__ import annotations

import numpy as np

from app.ai.retriever import RetrievedChunk, _mmr_select, _reciprocal_rank_fusion
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
    selected = _mmr_select(q, chunks, final_k=3, lambda_mult=0.5)
    ids = [c.chunk_id for c in selected]
    assert ids[0] == "c0"
    assert "c1" not in ids  # the duplicate is excluded for diversity
    assert len(set(ids)) == 3
