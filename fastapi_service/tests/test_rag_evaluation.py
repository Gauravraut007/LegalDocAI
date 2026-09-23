from __future__ import annotations

from app.ai.evaluation import (
    evaluate_answer_quality,
    retrieval_coverage_score,
    retrieval_quality_score,
)


def test_retrieval_coverage_score_counts_keyword_overlap() -> None:
    score = retrieval_coverage_score(
        "termination payment liability",
        [
            "The termination clause governs payment obligations.",
            "Liability is capped at one year.",
        ],
    )
    assert 0.0 < score <= 1.0


def test_retrieval_quality_score_prioritizes_expected_terms() -> None:
    score = retrieval_quality_score(
        "What is the confidentiality period?",
        [
            "The confidentiality period lasts for two years.",
            "The parties agree to payment terms.",
        ],
        expected_terms=["confidentiality", "period", "two", "years"],
    )
    assert 0.0 < score <= 1.0


def test_evaluate_answer_quality_tracks_grounding_and_coverage() -> None:
    result = evaluate_answer_quality(
        "The termination payment is due within 30 days.",
        [
            "Termination payment is due within 30 days.",
            "The parties agree to a confidentiality obligation.",
        ],
        "What is the termination payment timeline?",
    )

    assert "grounding_score" in result
    assert "coverage_score" in result
    assert 0.0 <= result["grounding_score"] <= 1.0
    assert 0.0 <= result["coverage_score"] <= 1.0
