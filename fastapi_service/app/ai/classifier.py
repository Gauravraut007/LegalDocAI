"""Rule-based document-type classifier.

Outputs one of: contract | nda | filing | general | unknown.
The first ~8000 characters of OCR text are inspected for keyword/regex hits.
"""
from __future__ import annotations

import re
from typing import Final

from app.models.document import DocType

_HEAD_WINDOW = 8000

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


def classify_document(text: str) -> DocType:
    """Return a DocType from a piece of OCR text using regex heuristics."""
    if not text or not text.strip():
        return DocType.unknown

    window = text[:_HEAD_WINDOW]

    nda_hits = _score(NDA_PATTERNS, window)
    filing_hits = _score(FILING_PATTERNS, window)
    contract_hits = _score(CONTRACT_PATTERNS, window)
    general_hits = _score(GENERAL_LEGAL_HINTS, window)

    # NDA wins early: very specific phrasing.
    if nda_hits >= 1:
        return DocType.nda
    if filing_hits >= 2:
        return DocType.filing
    if contract_hits >= 2:
        return DocType.contract
    if general_hits >= 2:
        return DocType.general
    return DocType.unknown
