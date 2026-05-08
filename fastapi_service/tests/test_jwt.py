"""JWT verification tests."""
from __future__ import annotations

import time
import uuid

import jwt as pyjwt
import pytest

from app.config import settings
from app.security.jwt import JWTError, decode_token


def _make_token(**overrides):
    now = int(time.time())
    payload = {
        "iss": settings.JWT_ISSUER,
        "iat": now,
        "exp": now + 60,
        "user_id": str(uuid.uuid4()),
        "email": "user@example.com",
        "workspace_ids": [str(uuid.uuid4())],
        "is_staff": False,
    }
    payload.update(overrides)
    key = overrides.pop("__key", settings.JWT_SIGNING_KEY)
    return pyjwt.encode(payload, key, algorithm=settings.JWT_ALGORITHM)


def test_decode_valid_token() -> None:
    tok = _make_token()
    user = decode_token(tok)
    assert user.email == "user@example.com"
    assert user.is_staff is False
    assert len(user.workspace_ids) == 1


def test_decode_expired_token() -> None:
    tok = _make_token(exp=int(time.time()) - 10)
    with pytest.raises(JWTError, match="expired"):
        decode_token(tok)


def test_decode_wrong_issuer() -> None:
    tok = _make_token(iss="not-django")
    with pytest.raises(JWTError):
        decode_token(tok)


def test_decode_wrong_signing_key() -> None:
    bad = pyjwt.encode(
        {
            "iss": settings.JWT_ISSUER,
            "exp": int(time.time()) + 60,
            "user_id": str(uuid.uuid4()),
            "email": "u@x",
            "workspace_ids": [],
            "is_staff": False,
        },
        "wrong-key",
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(JWTError):
        decode_token(bad)


def test_missing_required_claim() -> None:
    now = int(time.time())
    tok = pyjwt.encode(
        {"iss": settings.JWT_ISSUER, "exp": now + 60, "email": "u@x"},
        settings.JWT_SIGNING_KEY,
        algorithm=settings.JWT_ALGORITHM,
    )
    with pytest.raises(JWTError, match="missing claims"):
        decode_token(tok)


def test_can_access_document() -> None:
    user = decode_token(_make_token())
    other_owner = uuid.uuid4()
    other_workspace = uuid.uuid4()
    assert user.can_access_document(user.user_id, None) is True
    assert user.can_access_document(other_owner, user.workspace_ids[0]) is True
    assert user.can_access_document(other_owner, other_workspace) is False


def test_staff_can_access_anything() -> None:
    user = decode_token(_make_token(is_staff=True))
    assert user.can_access_document(uuid.uuid4(), uuid.uuid4()) is True
