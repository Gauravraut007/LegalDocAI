"""Unit tests for prompt selection."""
from app.ai.prompts import (
    CONTRACT_PROMPT,
    COURT_FILING_PROMPT,
    GENERAL_LEGAL_PROMPT,
    NDA_PROMPT,
    REFUSAL_PHRASE,
    select_prompt,
)
from app.models.document import DocType


def test_select_prompt_by_doc_type() -> None:
    assert select_prompt([DocType.contract]) == CONTRACT_PROMPT
    assert select_prompt([DocType.nda]) == NDA_PROMPT
    assert select_prompt([DocType.filing]) == COURT_FILING_PROMPT
    assert select_prompt([DocType.general]) == GENERAL_LEGAL_PROMPT
    assert select_prompt([DocType.unknown]) == GENERAL_LEGAL_PROMPT


def test_select_prompt_majority_vote() -> None:
    assert (
        select_prompt([DocType.contract, DocType.contract, DocType.nda])
        == CONTRACT_PROMPT
    )


def test_select_prompt_hint_overrides() -> None:
    assert (
        select_prompt([DocType.contract], doc_type_hint=DocType.nda) == NDA_PROMPT
    )


def test_select_prompt_override_wins() -> None:
    assert select_prompt([DocType.contract], override="custom") == "custom"


def test_refusal_phrase_in_prompts() -> None:
    for p in (CONTRACT_PROMPT, NDA_PROMPT, COURT_FILING_PROMPT, GENERAL_LEGAL_PROMPT):
        assert REFUSAL_PHRASE in p
