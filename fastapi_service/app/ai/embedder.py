"""Local sentence-transformers embedder.

- Singleton model loaded once per worker process (lazy).
- Adaptive batch size: starts at 32, halves on OOM-like errors.
- Vectors are L2-normalised so FAISS IndexFlatIP == cosine similarity.
"""
from __future__ import annotations

import logging
import threading
from typing import List, Optional

import numpy as np

from app.config import settings
from app.utils.retry import io_retry

logger = logging.getLogger(__name__)

_MODEL_LOCK = threading.Lock()
_MODEL_INSTANCE = None  # type: ignore[assignment]
_BGE_QUERY_PREFIX = "Represent this sentence for searching relevant passages: "


@io_retry(max_attempts=3, min_wait=1.0, max_wait=10.0)
def _load_model(model_name: str):
    from sentence_transformers import SentenceTransformer

    logger.info("Loading sentence-transformers model: %s", model_name)
    return SentenceTransformer(model_name)


def get_model():
    """Return a process-singleton SentenceTransformer instance."""
    global _MODEL_INSTANCE
    if _MODEL_INSTANCE is None:
        with _MODEL_LOCK:
            if _MODEL_INSTANCE is None:
                _MODEL_INSTANCE = _load_model(settings.EMBEDDING_MODEL_NAME)
    return _MODEL_INSTANCE


def _is_bge() -> bool:
    return "bge" in settings.EMBEDDING_MODEL_NAME.lower()


def _is_oom_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    if "out of memory" in msg or "cuda out of memory" in msg or "cublas" in msg:
        return True
    return exc.__class__.__name__ == "OutOfMemoryError"


class LocalEmbedder:
    """Local SBERT embedder with adaptive batching."""

    def __init__(self, model_name: Optional[str] = None) -> None:
        self.model_name = model_name or settings.EMBEDDING_MODEL_NAME
        self.dim = settings.EMBEDDING_DIM

    # ------------------------------------------------------- HIGH LEVEL
    def embed_documents(self, texts: List[str]) -> np.ndarray:
        """Embed a list of texts; returns float32 np.ndarray of shape (N, dim)."""
        if not texts:
            return np.zeros((0, self.dim), dtype=np.float32)
        return self._encode(texts, is_query=False, start_batch=32)

    def embed_query(self, query: str) -> np.ndarray:
        if not query.strip():
            raise ValueError("Query is empty")
        prefixed = (
            f"{_BGE_QUERY_PREFIX}{query}" if _is_bge() else query
        )
        vec = self._encode([prefixed], is_query=True, start_batch=1)
        return vec[0]

    # --------------------------------------------------------- INTERNAL
    def _encode(
        self,
        texts: List[str],
        *,
        is_query: bool,  # noqa: ARG002 - kept for future per-call behaviour
        start_batch: int,
    ) -> np.ndarray:
        model = get_model()
        batch_size = max(1, start_batch)
        vectors: list[np.ndarray] = []
        i = 0
        n = len(texts)
        while i < n:
            sub = texts[i : i + batch_size]
            try:
                arr = model.encode(
                    sub,
                    batch_size=batch_size,
                    normalize_embeddings=True,
                    show_progress_bar=False,
                    convert_to_numpy=True,
                )
                vectors.append(np.asarray(arr, dtype=np.float32))
                i += len(sub)
            except Exception as exc:  # noqa: BLE001
                if _is_oom_error(exc) and batch_size > 1:
                    new_size = max(1, batch_size // 2)
                    logger.warning(
                        "embedder OOM at batch=%d, retrying with batch=%d",
                        batch_size,
                        new_size,
                    )
                    batch_size = new_size
                    continue
                raise
        out = (
            np.concatenate(vectors, axis=0)
            if vectors
            else np.zeros((0, self.dim), dtype=np.float32)
        )
        if out.shape[1] != self.dim:
            raise RuntimeError(
                f"embedding dim mismatch: model={out.shape[1]} expected={self.dim}"
            )
        return out
