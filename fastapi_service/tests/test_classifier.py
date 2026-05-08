"""Unit tests for the rule-based classifier."""
from app.ai.classifier import classify_document
from app.models.document import DocType


def test_classifier_detects_nda() -> None:
    text = "MUTUAL NON-DISCLOSURE AGREEMENT between Foo and Bar..."
    assert classify_document(text) == DocType.nda


def test_classifier_detects_filing() -> None:
    text = (
        "IN THE DISTRICT COURT OF EXAMPLE.\n"
        "Plaintiff vs. Defendant. Case No. 12-345.\n"
        "Complaint for damages."
    )
    assert classify_document(text) == DocType.filing


def test_classifier_detects_contract() -> None:
    text = (
        "This Agreement is made on the 1st day of January.\n"
        "The parties hereto agree as follows. "
        "Term and Termination. Governing Law shall be Delaware."
    )
    assert classify_document(text) == DocType.contract


def test_classifier_unknown_for_empty() -> None:
    assert classify_document("") == DocType.unknown
