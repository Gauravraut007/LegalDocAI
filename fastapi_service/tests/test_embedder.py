"""Embedder smoke test (skipped unless the SBERT model is available locally)."""
from __future__ import annotations

import numpy as np
import pytest


def _model_available() -> bool:
    try:
        from sentence_transformers import SentenceTransformer  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.skipif(not _model_available(), reason="sentence-transformers not installed")
def test_local_embedder_dim_and_normalisation() -> None:
    from app.ai.embedder import LocalEmbedder
    from app.config import settings

    embedder = LocalEmbedder()
    vecs = embedder.embed_documents(["hello world", "another sentence"])
    assert vecs.shape == (2, settings.EMBEDDING_DIM)
    norms = np.linalg.norm(vecs, axis=1)
    assert np.allclose(norms, 1.0, atol=1e-3)


@pytest.mark.skipif(not _model_available(), reason="sentence-transformers not installed")
def test_embed_query_shape() -> None:
    from app.ai.embedder import LocalEmbedder
    from app.config import settings

    v = LocalEmbedder().embed_query("legal contract")
    assert v.shape == (settings.EMBEDDING_DIM,)
