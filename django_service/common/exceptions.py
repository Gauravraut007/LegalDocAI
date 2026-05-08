"""DRF exception handler producing RFC 7807 ``application/problem+json``-style errors."""
from __future__ import annotations

import logging
from typing import Any

from django.core.exceptions import PermissionDenied as DjangoPermissionDenied
from django.http import Http404
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler

logger = logging.getLogger(__name__)

_TYPE_PREFIX = "https://ldip.local/problems"


def _problem(
    *,
    title: str,
    status_code: int,
    detail: Any,
    code: str,
    request=None,
    extras: dict | None = None,
) -> Response:
    payload: dict[str, Any] = {
        "type": f"{_TYPE_PREFIX}/{code}",
        "title": title,
        "status": status_code,
        "detail": detail,
        "code": code,
    }
    if request is not None:
        try:
            payload["instance"] = request.build_absolute_uri()
        except Exception:  # noqa: BLE001
            pass
        rid = getattr(request, "request_id", None)
        if rid:
            payload["request_id"] = rid
    if extras:
        payload.update(extras)
    response = Response(payload, status=status_code)
    response["Content-Type"] = "application/problem+json"
    return response


def _normalize_detail(data):
    if isinstance(data, dict) and "detail" in data and len(data) == 1:
        return data["detail"]
    return data


def custom_exception_handler(exc, context):
    request = context.get("request") if context else None
    response = exception_handler(exc, context)

    if response is not None:
        return _problem(
            title=exc.__class__.__name__,
            status_code=response.status_code,
            detail=_normalize_detail(response.data),
            code=str(getattr(exc, "default_code", None) or response.status_code),
            request=request,
        )

    if isinstance(exc, Http404):
        return _problem(
            title="NotFound",
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc) or "resource not found",
            code="not_found",
            request=request,
        )
    if isinstance(exc, DjangoPermissionDenied):
        return _problem(
            title="PermissionDenied",
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc) or "permission denied",
            code="permission_denied",
            request=request,
        )

    logger.exception("Unhandled exception in DRF view")
    return _problem(
        title="InternalServerError",
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail=str(exc) or "internal server error",
        code="internal_error",
        request=request,
    )
