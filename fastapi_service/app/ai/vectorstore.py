"""Local FAISS vector store: one persisted index per document.

Files (under FAISS_INDEX_DIR):
    {document_id}.index       - FAISS index (IndexFlatIP or IndexHNSWFlat).
    {document_id}.meta.json   - {chunk_ids, model_name, dim, created_at, count, type}.

Atomic writes: every write goes to ``.tmp`` first, fsyncs, then ``os.replace`` swaps.
An LRU cache of open indexes lives in-process to avoid disk reads on hot search.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

import numpy as np

from app.config import settings

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------- CONSTANTS
HNSW_THRESHOLD = 50_000
HNSW_M = 32
LRU_CAPACITY = 16


# ---------------------------------------------------------------- HELPERS
def index_path(document_id: uuid.UUID | str) -> str:
    return os.path.join(settings.FAISS_INDEX_DIR, f"{document_id}.index")


def meta_path(document_id: uuid.UUID | str) -> str:
    return os.path.join(settings.FAISS_INDEX_DIR, f"{document_id}.meta.json")


def _normalize(arr: np.ndarray) -> np.ndarray:
    if arr.dtype != np.float32:
        arr = arr.astype(np.float32)
    norms = np.linalg.norm(arr, axis=1, keepdims=True)
    norms = np.where(norms == 0.0, 1.0, norms)
    return arr / norms


def _atomic_write_bytes(path: str, payload: bytes) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "wb") as fh:
        fh.write(payload)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _atomic_write_json(path: str, data: dict) -> None:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    _atomic_write_bytes(path, payload)


def _atomic_write_index(path: str, index) -> None:
    import faiss

    tmp = f"{path}.tmp"
    faiss.write_index(index, tmp)
    # FAISS already fsyncs internally on most platforms; replace atomically.
    os.replace(tmp, path)


# ---------------------------------------------------------------- DATA
@dataclass
class SearchHit:
    document_id: str
    chunk_id: str
    score: float
    rank: int = 0


@dataclass
class IndexMeta:
    document_id: str
    model_name: str
    dim: int
    chunk_ids: list[str] = field(default_factory=list)
    type: str = "flat"
    created_at: float = 0.0

    @property
    def vector_count(self) -> int:
        return len(self.chunk_ids)

    def to_dict(self) -> dict:
        return {
            "document_id": self.document_id,
            "model_name": self.model_name,
            "dim": self.dim,
            "chunk_ids": self.chunk_ids,
            "type": self.type,
            "count": len(self.chunk_ids),
            "created_at": self.created_at,
        }


# ---------------------------------------------------------------- LRU CACHE
class _IndexCache:
    """Thread-safe LRU cache of (faiss.Index, IndexMeta) pairs."""

    def __init__(self, capacity: int = LRU_CAPACITY) -> None:
        self._capacity = capacity
        self._lock = threading.Lock()
        self._items: "OrderedDict[str, tuple]" = OrderedDict()

    def get(self, document_id: str) -> Optional[tuple]:
        with self._lock:
            item = self._items.get(document_id)
            if item is not None:
                self._items.move_to_end(document_id)
            return item

    def put(self, document_id: str, value: tuple) -> None:
        with self._lock:
            self._items[document_id] = value
            self._items.move_to_end(document_id)
            while len(self._items) > self._capacity:
                self._items.popitem(last=False)

    def evict(self, document_id: str) -> None:
        with self._lock:
            self._items.pop(document_id, None)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


_CACHE = _IndexCache()


# ---------------------------------------------------------------- STORE
class FaissVectorStore:
    """Per-document FAISS store backed by atomic-on-disk persistence."""

    def __init__(self) -> None:
        self.dim = settings.EMBEDDING_DIM
        os.makedirs(settings.FAISS_INDEX_DIR, exist_ok=True)

    # ------------------------------------------------------------ CREATE
    def create_index(
        self,
        document_id: uuid.UUID | str,
        *,
        dim: Optional[int] = None,
        expected_size: int = 0,
    ):
        import faiss

        d = dim or self.dim
        use_hnsw = expected_size >= HNSW_THRESHOLD
        if use_hnsw:
            idx = faiss.IndexHNSWFlat(d, HNSW_M, faiss.METRIC_INNER_PRODUCT)
        else:
            idx = faiss.IndexFlatIP(d)
        meta = IndexMeta(
            document_id=str(document_id),
            model_name=settings.EMBEDDING_MODEL_NAME,
            dim=d,
            chunk_ids=[],
            type="hnsw" if use_hnsw else "flat",
            created_at=time.time(),
        )
        self._persist(document_id, idx, meta)
        return idx, meta

    # ------------------------------------------------------------ ADD
    def add(
        self,
        document_id: uuid.UUID | str,
        vectors: np.ndarray,
        chunk_ids: Sequence[str],
    ) -> int:
        if len(chunk_ids) != vectors.shape[0]:
            raise ValueError(
                f"chunk_ids ({len(chunk_ids)}) and vectors ({vectors.shape[0]}) length mismatch"
            )
        if vectors.size == 0:
            return 0
        idx, meta = self.load(document_id) or self.create_index(
            document_id, expected_size=len(chunk_ids)
        )
        normalised = _normalize(vectors)
        if normalised.shape[1] != meta.dim:
            raise ValueError(
                f"vector dim {normalised.shape[1]} != index dim {meta.dim}"
            )
        idx.add(normalised)
        meta.chunk_ids.extend(str(c) for c in chunk_ids)
        self._persist(document_id, idx, meta)
        return int(vectors.shape[0])

    # ------------------------------------------------------------ SEARCH
    def search(
        self,
        document_ids: Iterable[uuid.UUID | str],
        query_vector: np.ndarray,
        k: int = 5,
    ) -> List[SearchHit]:
        if query_vector.ndim == 1:
            query_vector = query_vector.reshape(1, -1)
        q = _normalize(query_vector.astype(np.float32))
        all_hits: list[SearchHit] = []
        for doc_id in document_ids:
            loaded = self.load(doc_id)
            if loaded is None:
                continue
            idx, meta = loaded
            if idx.ntotal == 0:
                continue
            top = min(k, idx.ntotal)
            scores, ids = idx.search(q, top)
            for rank, (score, row) in enumerate(
                zip(scores[0].tolist(), ids[0].tolist())
            ):
                if row < 0 or row >= len(meta.chunk_ids):
                    continue
                all_hits.append(
                    SearchHit(
                        document_id=str(doc_id),
                        chunk_id=meta.chunk_ids[row],
                        score=float(score),
                        rank=rank,
                    )
                )
        all_hits.sort(key=lambda h: h.score, reverse=True)
        return all_hits[:k]

    # ------------------------------------------------------------ DELETE
    def delete_index(self, document_id: uuid.UUID | str) -> bool:
        removed = False
        for path in (index_path(document_id), meta_path(document_id)):
            if os.path.exists(path):
                try:
                    os.remove(path)
                    removed = True
                except OSError as exc:
                    logger.warning("failed to remove %s: %s", path, exc)
        _CACHE.evict(str(document_id))
        return removed

    # ------------------------------------------------------------ LOAD
    def load(self, document_id: uuid.UUID | str) -> Optional[tuple]:
        key = str(document_id)
        cached = _CACHE.get(key)
        if cached is not None:
            return cached

        i_path = index_path(document_id)
        m_path = meta_path(document_id)
        if not os.path.exists(i_path) or not os.path.exists(m_path):
            return None

        import faiss

        try:
            idx = faiss.read_index(i_path)
            with open(m_path, "r", encoding="utf-8") as fh:
                raw = json.load(fh)
            meta = IndexMeta(
                document_id=str(raw.get("document_id", document_id)),
                model_name=raw.get("model_name", settings.EMBEDDING_MODEL_NAME),
                dim=int(raw.get("dim", self.dim)),
                chunk_ids=list(raw.get("chunk_ids", [])),
                type=raw.get("type", "flat"),
                created_at=float(raw.get("created_at", 0.0)),
            )
        except Exception as exc:  # noqa: BLE001
            logger.error("failed to load FAISS index %s: %s", i_path, exc)
            return None

        _CACHE.put(key, (idx, meta))
        return idx, meta

    # ------------------------------------------------------------ INFO
    def get_info(self, document_id: uuid.UUID | str) -> dict:
        loaded = self.load(document_id)
        if loaded is None:
            return {"document_id": str(document_id), "exists": False}
        idx, meta = loaded
        return {
            "document_id": meta.document_id,
            "exists": True,
            "model_name": meta.model_name,
            "dim": meta.dim,
            "type": meta.type,
            "vector_count": int(idx.ntotal),
            "index_path": index_path(document_id),
        }

    # ------------------------------------------------------------ INTERNAL
    def _persist(
        self,
        document_id: uuid.UUID | str,
        idx,
        meta: IndexMeta,
    ) -> None:
        os.makedirs(settings.FAISS_INDEX_DIR, exist_ok=True)
        _atomic_write_index(index_path(document_id), idx)
        _atomic_write_json(meta_path(document_id), meta.to_dict())
        _CACHE.put(str(document_id), (idx, meta))


# ---------------------------------------------------------------- BACK-COMPAT
def collection_name_for(document_id: str) -> str:
    return f"doc_{str(document_id).replace('-', '')}"
