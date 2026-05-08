"""Throttles for sensitive endpoints."""
from __future__ import annotations

from rest_framework.throttling import AnonRateThrottle, ScopedRateThrottle


class LoginRateThrottle(AnonRateThrottle):
    """5 / minute by client IP for unauthenticated POST /login."""

    scope = "login"


class RegisterRateThrottle(AnonRateThrottle):
    scope = "register"


class PasswordResetRateThrottle(AnonRateThrottle):
    scope = "password_reset"


__all__ = [
    "LoginRateThrottle",
    "RegisterRateThrottle",
    "PasswordResetRateThrottle",
    "ScopedRateThrottle",
]
