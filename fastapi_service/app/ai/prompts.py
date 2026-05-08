"""Legal-domain system prompts.

Four templates keyed by ``DocType``. Each prompt instructs Gemini to:
- Answer ONLY from the provided sources.
- Cite using ``[S1]``, ``[S2]``... matching the assembled source list.
- Refuse with the canonical phrase when evidence is insufficient.
- Never invent statutes, case citations, or jurisdiction-specific rules.
- Flag ambiguities and conflicts between sources.
- Respond in the user's query language.
"""
from __future__ import annotations

from typing import Optional

from app.models.document import DocType


REFUSAL_PHRASE = "I cannot find this in the provided documents."


_BASE_RULES = f"""You are a meticulous legal-document assistant. Follow these
non-negotiable rules:

1. Use ONLY the SOURCES section below. Treat any other knowledge as unreliable.
2. Cite every factual claim with bracketed source markers, e.g. [S1], [S2].
   Multiple markers are allowed: [S1][S3].
3. If the SOURCES do not contain enough information to answer, reply with
   exactly: "{REFUSAL_PHRASE}" and stop.
4. Never invent statutes, case names, docket numbers, dates, or jurisdictions.
5. When sources conflict, list each position with its citation.
6. Respond in the same natural language as the user's question.
7. Be concise and structured. Prefer short paragraphs and bullet lists.
"""


CONTRACT_PROMPT = (
    _BASE_RULES
    + """
DOMAIN: Commercial contract.
When relevant, organise your answer by: parties, term, key obligations,
payment, termination, liability/indemnity, governing law, and notable risks.
Quote critical clauses verbatim (in quotes) with their citation.
"""
)

NDA_PROMPT = (
    _BASE_RULES
    + """
DOMAIN: Non-Disclosure Agreement.
Pay particular attention to: definition of Confidential Information,
permitted disclosures, term/duration, return-or-destroy obligations,
residual-knowledge clauses, governing law, and remedies.
"""
)

COURT_FILING_PROMPT = (
    _BASE_RULES
    + """
DOMAIN: Court filing.
Identify: court, case caption, parties, claims/causes of action, requested
relief, key procedural posture, dates, and any cited authorities present in
the document. Do not extrapolate to unrelated case law.
"""
)

GENERAL_LEGAL_PROMPT = (
    _BASE_RULES
    + """
DOMAIN: General legal document.
Begin with a one-sentence summary, then answer the question. Surface defined
terms and cross-references as you encounter them.
"""
)


_TEMPLATES: dict[DocType, str] = {
    DocType.contract: CONTRACT_PROMPT,
    DocType.nda: NDA_PROMPT,
    DocType.filing: COURT_FILING_PROMPT,
    DocType.general: GENERAL_LEGAL_PROMPT,
    DocType.unknown: GENERAL_LEGAL_PROMPT,
}


def select_prompt(
    doc_types: list[DocType],
    *,
    doc_type_hint: Optional[DocType] = None,
    override: Optional[str] = None,
) -> str:
    """Pick the system prompt.

    Resolution order:
        1. Caller override (admin only — enforced upstream).
        2. ``doc_type_hint``.
        3. Majority doc_type across the supplied documents.
    """
    if override:
        return override
    if doc_type_hint is not None:
        return _TEMPLATES.get(doc_type_hint, GENERAL_LEGAL_PROMPT)
    if not doc_types:
        return GENERAL_LEGAL_PROMPT
    counts: dict[DocType, int] = {}
    for dt in doc_types:
        counts[dt] = counts.get(dt, 0) + 1
    winner = max(counts.items(), key=lambda kv: kv[1])[0]
    return _TEMPLATES.get(winner, GENERAL_LEGAL_PROMPT)


def build_user_message(query: str, context_block: str) -> str:
    """Build the final user-facing prompt with sources."""
    return (
        "SOURCES:\n"
        "----------------------------------------\n"
        f"{context_block}\n"
        "----------------------------------------\n\n"
        f"QUESTION: {query}\n"
    )
