"""Helpers to mint password-reset tokens stored in the cache."""
from __future__ import annotations

import secrets
from datetime import timedelta

from django.core.cache import cache
from django.utils import timezone

_NS = "pwreset:"
DEFAULT_TTL = int(timedelta(hours=1).total_seconds())


def make_token(user_id) -> str:
    token = secrets.token_urlsafe(32)
    cache.set(_NS + token, str(user_id), timeout=DEFAULT_TTL)
    return token


def consume_token(token: str) -> str | None:
    key = _NS + token
    user_id = cache.get(key)
    if user_id:
        cache.delete(key)
        return str(user_id)
    return None


def utcnow():
    return timezone.now()
