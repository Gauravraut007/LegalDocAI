"""JWT verification (HS256) for tokens issued by the Django service.

Tokens are issued by ``rest_framework_simplejwt`` in the Django service with a
shared signing key. We verify locally — no network call. Required claims:

    iss          : "django-service"
    exp          : standard
    user_id      : str (UUID)
    email        : str
    workspace_ids: list[str | UUID]
    is_staff     : bool

A valid token is wrapped in :class:`AuthenticatedUser` and made available to
route handlers via the FastAPI dependency :func:`get_current_user`.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Iterable, List, Optional

import jwt as pyjwt
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.config import settings


class JWTError(Exception):
    """Raised by :func:`decode_token` for any verification failure."""


@dataclass
class AuthenticatedUser:
    user_id: uuid.UUID
    email: str
    workspace_ids: List[uuid.UUID] = field(default_factory=list)
    is_staff: bool = False
    raw: dict = field(default_factory=dict)

    def can_access_document(
        self,
        owner_user_id: uuid.UUID,
        workspace_id: Optional[uuid.UUID],
    ) -> bool:
        if self.is_staff:
            return True
        if owner_user_id == self.user_id:
            return True
        if workspace_id is not None and workspace_id in self.workspace_ids:
            return True
        return False


_REQUIRED_CLAIMS = ("user_id", "email", "workspace_ids", "is_staff")


def _coerce_uuid(val: object, *, field_name: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise JWTError(f"invalid UUID in claim '{field_name}': {val!r}") from exc


def _coerce_workspace_ids(val: object) -> List[uuid.UUID]:
    if val is None:
        return []
    if not isinstance(val, Iterable) or isinstance(val, (str, bytes)):
        raise JWTError("workspace_ids must be a list")
    out: List[uuid.UUID] = []
    for item in val:
        try:
            out.append(uuid.UUID(str(item)))
        except (ValueError, TypeError) as exc:
            raise JWTError(f"invalid UUID in workspace_ids: {item!r}") from exc
    return out


def decode_token(token: str) -> AuthenticatedUser:
    """Decode + verify a JWT. Raises :class:`JWTError` on any failure."""
    try:
        payload = pyjwt.decode(
            token,
            settings.JWT_SIGNING_KEY,
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={"require": ["exp", "iss"]},
        )
    except pyjwt.ExpiredSignatureError as exc:
        raise JWTError("token expired") from exc
    except pyjwt.InvalidIssuerError as exc:
        raise JWTError("invalid issuer") from exc
    except pyjwt.InvalidTokenError as exc:
        raise JWTError(f"invalid token: {exc}") from exc

    missing = [c for c in _REQUIRED_CLAIMS if c not in payload]
    if missing:
        raise JWTError(f"missing claims: {missing}")

    return AuthenticatedUser(
        user_id=_coerce_uuid(payload["user_id"], field_name="user_id"),
        email=str(payload["email"]),
        workspace_ids=_coerce_workspace_ids(payload.get("workspace_ids")),
        is_staff=bool(payload.get("is_staff", False)),
        raw=payload,
    )


# ---------------------------------------------------------------- FastAPI ---
_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> AuthenticatedUser:
    """FastAPI dependency that extracts and verifies the bearer token."""
    if credentials is None or not credentials.credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    try:
        user = decode_token(credentials.credentials)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    request.state.user = user
    return user


async def require_staff(
    user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    if not user.is_staff:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="staff privileges required",
        )
    return user
