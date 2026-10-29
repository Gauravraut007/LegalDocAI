"""Unit tests for FaissVectorStore using synthetic vectors (no model load)."""
from __future__ import annotations

import os
import uuid

import numpy as np
import pytest

faiss = pytest.importorskip("faiss")

from app.ai.vectorstore import FaissVectorStore, index_path, meta_path  # noqa: E402
from app.config import settings  # noqa: E402


def _vecs(n: int, dim: int) -> np.ndarray:
    rng = np.random.default_rng(0)
    return rng.standard_normal((n, dim)).astype(np.float32)


def test_create_add_search_and_persist() -> None:
    store = FaissVectorStore()
    doc_id = str(uuid.uuid4())
    dim = settings.EMBEDDING_DIM

    vectors = _vecs(20, dim)
    chunk_ids = [str(uuid.uuid4()) for _ in range(20)]
    added = store.add(doc_id, vectors, chunk_ids)
    assert added == 20

    # Files exist on disk.
    assert os.path.exists(index_path(doc_id))
    assert os.path.exists(meta_path(doc_id))

    # Search returns at most k hits.
    q = vectors[0].copy()
    hits = store.search([doc_id], q, k=5)
    assert 1 <= len(hits) <= 5
    # The closest hit should be the chunk we used as a query.
    assert hits[0].chunk_id == chunk_ids[0]

    # Cleanup.
    assert store.delete_index(doc_id)
    assert not os.path.exists(index_path(doc_id))


def test_load_round_trip() -> None:
    store = FaissVectorStore()
    doc_id = str(uuid.uuid4())
    vectors = _vecs(5, settings.EMBEDDING_DIM)
    ids = [str(uuid.uuid4()) for _ in range(5)]
    store.add(doc_id, vectors, ids)

    loaded = store.load(doc_id)
    assert loaded is not None
    idx, meta = loaded
    assert idx.ntotal == 5
    assert meta.chunk_ids == ids
    store.delete_index(doc_id)


def test_get_vectors_reconstructs_and_tracks_appended_ids(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "FAISS_INDEX_DIR", str(tmp_path))
    store = FaissVectorStore()
    doc_id = str(uuid.uuid4())
    vectors = _vecs(2, settings.EMBEDDING_DIM)
    ids = ["first", "second"]
    store.add(doc_id, vectors, ids)

    initial = store.get_vectors([(doc_id, "first"), (doc_id, "missing")])
    expected = vectors / np.linalg.norm(vectors, axis=1, keepdims=True)
    assert set(initial) == {(doc_id, "first")}
    np.testing.assert_allclose(initial[(doc_id, "first")], expected[0])

    appended = _vecs(1, settings.EMBEDDING_DIM)
    store.add(doc_id, appended, ["third"])
    after_append = store.get_vectors([(doc_id, "third")])
    expected_appended = appended[0] / np.linalg.norm(appended[0])
    np.testing.assert_allclose(after_append[(doc_id, "third")], expected_appended)
    store.delete_index(doc_id)
