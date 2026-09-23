"""Rule-based document-type classifier.

Outputs one of: contract | nda | filing | general | unknown.
The first ~8000 characters of OCR text are inspected for keyword/regex hits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Final

from app.models.document import DocType

_HEAD_WINDOW = 8000


@dataclass(frozen=True)
class ClassificationResult:
    doc_type: DocType
    confidence: float
    reason: str

NDA_PATTERNS: Final = [
    re.compile(r"non[\s\-]?disclosure\s+agreement", re.IGNORECASE),
    re.compile(r"\bconfidentiality\s+agreement\b", re.IGNORECASE),
    re.compile(r"\bmutual\s+nda\b", re.IGNORECASE),
]

FILING_PATTERNS: Final = [
    re.compile(r"\bplaintiff\b", re.IGNORECASE),
    re.compile(r"\bdefendant\b", re.IGNORECASE),
    re.compile(r"\bin\s+the\s+(?:supreme|district|high|circuit|superior)\s+court\b", re.IGNORECASE),
    re.compile(r"\bcase\s+no\.?\s*[:#]?", re.IGNORECASE),
    re.compile(r"\bvs\.?\b|\bversus\b", re.IGNORECASE),
    re.compile(r"\bcomplaint\b|\bmotion\s+to\b|\bbrief\s+in\s+support\b", re.IGNORECASE),
]

CONTRACT_PATTERNS: Final = [
    re.compile(r"\bagreement\s+made\s+(?:on|this)\b", re.IGNORECASE),
    re.compile(r"\bthis\s+agreement\b", re.IGNORECASE),
    re.compile(r"\bservices?\s+agreement\b", re.IGNORECASE),
    re.compile(r"\bmaster\s+services?\s+agreement\b", re.IGNORECASE),
    re.compile(r"\b(parties|party)\s+hereto\b", re.IGNORECASE),
    re.compile(r"\bterm\s+and\s+termination\b", re.IGNORECASE),
    re.compile(r"\bgoverning\s+law\b", re.IGNORECASE),
]

GENERAL_LEGAL_HINTS: Final = [
    re.compile(r"\bwhereas\b", re.IGNORECASE),
    re.compile(r"\bnow,?\s+therefore\b", re.IGNORECASE),
    re.compile(r"\bin\s+witness\s+whereof\b", re.IGNORECASE),
    re.compile(r"\bjurisdiction\b", re.IGNORECASE),
    re.compile(r"\bindemnif", re.IGNORECASE),
]


def _score(patterns: list[re.Pattern[str]], window: str) -> int:
    return sum(1 for p in patterns if p.search(window))


def classify_document_with_confidence(text: str) -> ClassificationResult:
    """Classify a document and return a confidence score for routing and evals."""
    if not text or not text.strip():
        return ClassificationResult(DocType.unknown, 0.0, "empty_text")

    window = text[:_HEAD_WINDOW]
    nda_hits = _score(NDA_PATTERNS, window)
    filing_hits = _score(FILING_PATTERNS, window)
    contract_hits = _score(CONTRACT_PATTERNS, window)
    general_hits = _score(GENERAL_LEGAL_HINTS, window)

    candidates = [
        (DocType.nda, nda_hits, "nda_patterns"),
        (DocType.filing, filing_hits, "filing_patterns"),
        (DocType.contract, contract_hits, "contract_patterns"),
        (DocType.general, general_hits, "general_legal_patterns"),
    ]

    best_doc_type, best_hits, reason = max(candidates, key=lambda item: item[1])
    if best_hits <= 0:
        return ClassificationResult(DocType.unknown, 0.12, "no_match")

    # Preserve the original routing thresholds so the classifier remains stable,
    # while exposing a confidence score for monitoring and evaluation.
    if best_doc_type == DocType.nda and nda_hits >= 1:
        return ClassificationResult(
            DocType.nda,
            round(min(0.99, 0.62 + (nda_hits * 0.12)), 4),
            reason,
        )
    if best_doc_type == DocType.filing and filing_hits >= 2:
        return ClassificationResult(
            DocType.filing,
            round(min(0.99, 0.65 + (filing_hits * 0.1)), 4),
            reason,
        )
    if best_doc_type == DocType.contract and contract_hits >= 2:
        return ClassificationResult(
            DocType.contract,
            round(min(0.99, 0.68 + (contract_hits * 0.1)), 4),
            reason,
        )
    if best_doc_type == DocType.general and general_hits >= 2:
        return ClassificationResult(
            DocType.general,
            round(min(0.99, 0.6 + (general_hits * 0.1)), 4),
            reason,
        )

    return ClassificationResult(DocType.unknown, 0.18, "weak_match")


def classify_document(text: str) -> DocType:
    """Return a DocType from a piece of OCR text using regex heuristics."""
    return classify_document_with_confidence(text).doc_type


__all__ = ["ClassificationResult", "classify_document", "classify_document_with_confidence"]
