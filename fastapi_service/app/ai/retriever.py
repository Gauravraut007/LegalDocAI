"""Retriever: per-query FAISS search → RRF fusion → cross-encoder rerank → MMR.

All steps are local. The cross-encoder model is lazy-loaded once per process.
"""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Iterable, List, Optional, Sequence

import numpy as np

from app.ai.embedder import LocalEmbedder
from app.ai.vectorstore import FaissVectorStore, SearchHit
from app.config import settings
from app.utils import metrics

logger = logging.getLogger(__name__)


# =========================================================================
# Data
# =========================================================================
@dataclass
class RetrievedChunk:
    document_id: str
    chunk_id: str
    score: float
    rank: int = 0
    rerank_score: Optional[float] = None
    text: str = ""
    section_path: Optional[str] = None
    page_start: Optional[int] = None
    page_end: Optional[int] = None
    filename: Optional[str] = None
    embedding: Optional[np.ndarray] = field(default=None, repr=False)

    @property
    def best_score(self) -> float:
        return self.rerank_score if self.rerank_score is not None else self.score


# =========================================================================
# Cross-encoder reranker (lazy singleton)
# =========================================================================
_RERANKER = None
_RERANKER_LOCK = threading.Lock()


def _get_reranker():
    global _RERANKER
    if _RERANKER is None:
        with _RERANKER_LOCK:
            if _RERANKER is None:
                from sentence_transformers import CrossEncoder

                logger.info("loading reranker: %s", settings.RERANKER_MODEL_NAME)
                _RERANKER = CrossEncoder(settings.RERANKER_MODEL_NAME)
    return _RERANKER


# =========================================================================
# Retriever
# =========================================================================
class Retriever:
    def __init__(
        self,
        *,
        embedder: Optional[LocalEmbedder] = None,
        vector_store: Optional[FaissVectorStore] = None,
    ) -> None:
        self.embedder = embedder or LocalEmbedder()
        self.store = vector_store or FaissVectorStore()

    # ---------------------------------------------------------- public
    def retrieve(
        self,
        *,
        queries: Sequence[str],
        document_ids: Sequence[uuid.UUID | str],
        per_query_top_k: Optional[int] = None,
        rrf_k: Optional[int] = None,
    ) -> List[RetrievedChunk]:
        """Run multi-query FAISS search and RRF-fuse the results."""
        if not queries or not document_ids:
            return []
        per_query_top_k = per_query_top_k or settings.RAG_PER_QUERY_TOP_K
        rrf_k = rrf_k or settings.RAG_RRF_K

        t0 = time.monotonic()
        # Embed all queries in a single batch.
        q_vectors = self.embedder.embed_documents(list(queries))

        # Run FAISS search per (query, doc).
        per_query_hits: list[list[SearchHit]] = []
        for q_idx in range(q_vectors.shape[0]):
            qv = q_vectors[q_idx]
            hits = self.store.search(
                document_ids, qv.reshape(1, -1), k=per_query_top_k
            )
            per_query_hits.append(hits)

        fused = _reciprocal_rank_fusion(per_query_hits, k=rrf_k)
        metrics.RETRIEVAL_LATENCY.observe(time.monotonic() - t0)
        return fused

    # ---------------------------------------------------------- rerank
    def rerank(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        *,
        top_k: Optional[int] = None,
    ) -> List[RetrievedChunk]:
        if not chunks:
            return []
        top_k = top_k or settings.RAG_RERANK_TOP_K
        if not settings.RAG_ENABLE_RERANK:
            return chunks[:top_k]

        try:
            t0 = time.monotonic()
            reranker = _get_reranker()
            pairs = [(query, c.text or "") for c in chunks]
            scores = reranker.predict(pairs).tolist()
            for c, s in zip(chunks, scores):
                c.rerank_score = float(s)
            chunks.sort(key=lambda c: c.rerank_score or -1e9, reverse=True)
            metrics.RERANK_LATENCY.observe(time.monotonic() - t0)
            return chunks[:top_k]
        except Exception as exc:  # noqa: BLE001
            logger.warning("reranker_unavailable, falling back: %s", exc)
            return chunks[:top_k]

    # ---------------------------------------------------------- diversify
    def mmr(
        self,
        query: str,
        chunks: List[RetrievedChunk],
        *,
        final_k: Optional[int] = None,
        lambda_mult: Optional[float] = None,
    ) -> List[RetrievedChunk]:
        final_k = final_k or settings.RAG_FINAL_K
        lambda_mult = (
            lambda_mult if lambda_mult is not None else settings.RAG_MMR_LAMBDA
        )
        if not chunks:
            return []
        if final_k >= len(chunks):
            return chunks

        # Compute embeddings for any chunks missing them.
        missing = [c for c in chunks if c.embedding is None]
        if missing:
            vecs = self.embedder.embed_documents([c.text or "" for c in missing])
            for c, v in zip(missing, vecs):
                c.embedding = v
        q_vec = self.embedder.embed_query(query)

        return _mmr_select(q_vec, chunks, final_k, lambda_mult)


# =========================================================================
# RRF
# =========================================================================
def _reciprocal_rank_fusion(
    rankings: Iterable[list[SearchHit]], *, k: int = 60
) -> List[RetrievedChunk]:
    """Aggregate multiple ranked lists with reciprocal rank fusion."""
    pool: dict[tuple[str, str], RetrievedChunk] = {}
    fused: dict[tuple[str, str], float] = {}
    for ranking in rankings:
        for rank, hit in enumerate(ranking):
            key = (hit.document_id, hit.chunk_id)
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank + 1)
            if key not in pool:
                pool[key] = RetrievedChunk(
                    document_id=hit.document_id,
                    chunk_id=hit.chunk_id,
                    score=hit.score,
                )
            else:
                # Keep the best (highest) raw FAISS score across queries.
                pool[key].score = max(pool[key].score, hit.score)
    items = sorted(pool.values(), key=lambda c: fused[(c.document_id, c.chunk_id)], reverse=True)
    for i, c in enumerate(items):
        c.rank = i
        c.score = fused[(c.document_id, c.chunk_id)]
    return items


# =========================================================================
# MMR
# =========================================================================
def _mmr_select(
    query_vec: np.ndarray,
    chunks: List[RetrievedChunk],
    final_k: int,
    lambda_mult: float,
) -> List[RetrievedChunk]:
    embs = np.stack([c.embedding for c in chunks]).astype(np.float32)
    q = query_vec.astype(np.float32).reshape(1, -1)
    # Cosine similarity (vectors are already normalised by the embedder).
    sim_to_query = (embs @ q.T).flatten()
    sim_pairwise = embs @ embs.T

    selected: list[int] = []
    candidates = list(range(len(chunks)))
    while candidates and len(selected) < final_k:
        if not selected:
            best = int(np.argmax(sim_to_query[candidates]))
            selected.append(candidates.pop(best))
            continue
        best_score = -1e9
        best_idx_in_candidates = -1
        for i, cand in enumerate(candidates):
            redundancy = float(np.max(sim_pairwise[cand, selected]))
            score = lambda_mult * float(sim_to_query[cand]) - (1 - lambda_mult) * redundancy
            if score > best_score:
                best_score = score
                best_idx_in_candidates = i
        selected.append(candidates.pop(best_idx_in_candidates))

    return [chunks[i] for i in selected]


__all__ = ["Retriever", "RetrievedChunk", "_reciprocal_rank_fusion", "_mmr_select"]
