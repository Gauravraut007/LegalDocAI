"""Custom middleware for Django."""
from __future__ import annotations

import uuid

import structlog


class RequestIDMiddleware:
    """Attach ``X-Request-ID`` and bind structlog context for the request."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        rid = request.META.get("HTTP_X_REQUEST_ID") or uuid.uuid4().hex
        request.request_id = rid
        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=rid,
            path=request.path,
            method=request.method,
        )
        try:
            response = self.get_response(request)
        finally:
            structlog.contextvars.clear_contextvars()
        response["X-Request-ID"] = rid
        return response
