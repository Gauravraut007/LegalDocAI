"""Lightweight RAG quality helpers for grounding and retrieval checks.

These utilities are intentionally simple and dependency-free so they can be used
in CI and local evaluation without requiring a heavy ML stack.
"""
from __future__ import annotations

import math
import re
from typing import Iterable, Sequence

_TOKEN_RE = re.compile(r"[A-Za-z0-9']+")


def _tokenize(text: str) -> set[str]:
    return {token.lower() for token in _TOKEN_RE.findall(text or "") if len(token) > 1}


def retrieval_coverage_score(query: str, candidate_chunks: Sequence[str]) -> float:
    """Return an overlap score in [0, 1] for a query against candidate text."""
    if not query or not candidate_chunks:
        return 0.0

    query_tokens = _tokenize(query)
    if not query_tokens:
        return 0.0

    scores = []
    for chunk in candidate_chunks:
        chunk_tokens = _tokenize(chunk)
        if not chunk_tokens:
            continue
        overlap = len(query_tokens & chunk_tokens)
        score = overlap / max(len(query_tokens), 1)
        scores.append(score)

    return float(sum(scores) / max(len(scores), 1))


def retrieval_quality_score(
    query: str,
    retrieved_chunks: Sequence[str],
    expected_terms: Sequence[str] | None = None,
) -> float:
    """Score retrieval quality with a simple precision/coverage heuristic.

    If expected_terms are supplied, they are treated as the target legal keywords
    we expect the retrieval to surface. Otherwise we use query-token overlap with
    the retrieved chunks.
    """
    if not query or not retrieved_chunks:
        return 0.0

    query_tokens = _tokenize(query)
    if expected_terms is not None:
        target_tokens = {token.lower() for token in _TOKEN_RE.findall(" ".join(expected_terms)) if len(token) > 1}
    else:
        target_tokens = query_tokens

    if not target_tokens:
        return 0.0

    hits = 0
    for chunk in retrieved_chunks:
        chunk_tokens = _tokenize(chunk)
        if not chunk_tokens:
            continue
        hits += len(target_tokens & chunk_tokens)

    if not retrieved_chunks:
        return 0.0

    precision = hits / max(sum(len(_tokenize(chunk)) for chunk in retrieved_chunks), 1)
    coverage = hits / max(len(target_tokens), 1)
    return round(min(1.0, (0.6 * precision) + (0.4 * coverage)), 4)


def ground_answer_in_sources(answer: str, sources: Sequence[str]) -> float:
    """Measure how much the answer vocabulary overlaps with the source material."""
    if not answer or not sources:
        return 0.0

    answer_tokens = _tokenize(answer)
    if not answer_tokens:
        return 0.0

    source_tokens = set()
    for source in sources:
        source_tokens |= _tokenize(source)

    if not source_tokens:
        return 0.0

    overlap = len(answer_tokens & source_tokens)
    return overlap / max(len(answer_tokens), 1)


def evaluate_answer_quality(
    answer: str,
    sources: Sequence[str],
    query: str,
) -> dict:
    """Return a compact evaluation payload for a single RAG answer."""
    coverage = retrieval_coverage_score(query, list(sources))
    grounding = ground_answer_in_sources(answer, list(sources))
    answer_words = len(_tokenize(answer))
    quality = 0.5 * coverage + 0.5 * grounding
    return {
        "coverage_score": round(coverage, 4),
        "grounding_score": round(grounding, 4),
        "overall_score": round(quality, 4),
        "answer_word_count": answer_words,
    }


__all__ = [
    "ground_answer_in_sources",
    "evaluate_answer_quality",
    "retrieval_coverage_score",
    "retrieval_quality_score",
]
