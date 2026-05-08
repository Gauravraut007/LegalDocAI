"""JWT authentication middleware for Channels (WebSocket).

Reads the access token from one of:
    1. ``?token=...`` query parameter
    2. ``Sec-WebSocket-Protocol: bearer, <token>`` subprotocol header
    3. ``Authorization: Bearer <token>`` HTTP header (uncommon for browsers)

If a valid token is found, ``scope["user"]`` is populated with the resolved
Django user, otherwise an :class:`AnonymousUser` is set.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.middleware import BaseMiddleware
from django.contrib.auth import get_user_model
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
from rest_framework_simplejwt.tokens import UntypedToken


@database_sync_to_async
def _get_user(user_id):
    User = get_user_model()
    try:
        return User.objects.get(pk=user_id)
    except User.DoesNotExist:
        return AnonymousUser()


def _extract_token(scope: dict) -> str | None:
    qs = parse_qs((scope.get("query_string") or b"").decode("utf-8"))
    if "token" in qs and qs["token"]:
        return qs["token"][0]
    headers = dict(scope.get("headers") or [])
    proto = headers.get(b"sec-websocket-protocol")
    if proto:
        parts = [p.strip() for p in proto.decode("utf-8").split(",")]
        if len(parts) >= 2 and parts[0].lower() == "bearer":
            return parts[1]
    auth = headers.get(b"authorization")
    if auth:
        text = auth.decode("utf-8")
        if text.lower().startswith("bearer "):
            return text.split(" ", 1)[1].strip()
    return None


class JWTAuthMiddleware(BaseMiddleware):
    """Resolve ``scope['user']`` from a simplejwt access token."""

    async def __call__(self, scope: dict, receive: Any, send: Any):
        token = _extract_token(scope)
        scope["user"] = AnonymousUser()
        scope["jwt_token"] = None
        scope["jwt_payload"] = None
        if token:
            try:
                validated = UntypedToken(token)
                scope["jwt_token"] = token
                scope["jwt_payload"] = dict(validated.payload)
                user_id = validated.payload.get("user_id")
                if user_id:
                    scope["user"] = await _get_user(user_id)
            except (InvalidToken, TokenError):
                pass
        return await super().__call__(scope, receive, send)


def JWTAuthMiddlewareStack(inner):
    return JWTAuthMiddleware(inner)
