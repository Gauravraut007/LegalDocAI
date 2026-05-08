"""Pytest configuration + fixtures for the FastAPI service tests."""
from __future__ import annotations

import os
import sys
import uuid
from pathlib import Path

import pytest

# Make `app.*` importable when running tests from repo root.
THIS_DIR = Path(__file__).resolve().parent
SVC_ROOT = THIS_DIR.parent
sys.path.insert(0, str(SVC_ROOT))

# Override paths to a writable temp area BEFORE importing app modules.
_TMP_ROOT = THIS_DIR / "_tmp"
_TMP_ROOT.mkdir(exist_ok=True)
os.environ.setdefault("UPLOAD_DIR", str(_TMP_ROOT / "uploads"))
os.environ.setdefault("PROCESSED_DIR", str(_TMP_ROOT / "processed"))
os.environ.setdefault("FAISS_INDEX_DIR", str(_TMP_ROOT / "faiss"))
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("SYNC_DATABASE_URL", "sqlite:///:memory:")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/15")

for d in ("UPLOAD_DIR", "PROCESSED_DIR", "FAISS_INDEX_DIR"):
    os.makedirs(os.environ[d], exist_ok=True)

FIXTURES_DIR = THIS_DIR / "fixtures"
FIXTURES_DIR.mkdir(exist_ok=True)


@pytest.fixture(scope="session")
def fixtures_dir() -> Path:
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def sample_pdf(fixtures_dir: Path) -> Path:
    """Create a tiny sample PDF on first use using reportlab."""
    out = fixtures_dir / "sample.pdf"
    if out.exists():
        return out
    try:
        from reportlab.lib.pagesizes import letter
        from reportlab.pdfgen import canvas
    except Exception:  # noqa: BLE001
        pytest.skip("reportlab not installed")
    c = canvas.Canvas(str(out), pagesize=letter)
    c.setFont("Helvetica", 12)
    text = (
        "MUTUAL NON-DISCLOSURE AGREEMENT\n"
        "This Agreement is made by and between the parties hereto. "
        "Section 1. Definitions. Confidential Information shall mean any data "
        "disclosed by one party to the other. Section 2. Obligations. The "
        "receiving party agrees to maintain confidentiality. Section 3. Term. "
        "This agreement shall remain in effect for two years."
    )
    y = 750
    for line in text.split(". "):
        c.drawString(72, y, line.strip() + ".")
        y -= 18
    c.showPage()
    c.save()
    return out


@pytest.fixture
def sample_text() -> str:
    return (
        "MUTUAL NON-DISCLOSURE AGREEMENT\n\n"
        "This Agreement is made by and between Acme Corp and Foo Inc.\n\n"
        "Section 1. Definitions.\n"
        "Confidential Information means any data disclosed by one party to the other "
        "in connection with the Purpose. The receiving party shall hold all such "
        "Confidential Information in trust and shall not disclose it to any third "
        "party without prior written consent.\n\n"
        "Section 2. Obligations.\n"
        "The receiving party agrees to maintain confidentiality and to use the same "
        "degree of care that it uses to protect its own confidential information.\n\n"
        "Section 3. Term.\n"
        "This Agreement shall commence on the Effective Date and continue for a "
        "period of two (2) years thereafter.\n\n"
        "(a) Notwithstanding the foregoing, certain obligations shall survive "
        "termination.\n"
        "(b) Each party retains its own intellectual property.\n"
    )


@pytest.fixture
def doc_id() -> str:
    return str(uuid.uuid4())
