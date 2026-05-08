"""Health endpoints for the Django service."""
from __future__ import annotations

from datetime import datetime, timezone

from django.http import JsonResponse


def healthz(request):
    deps: dict[str, str] = {}

    # Postgres
    try:
        from django.db import connection

        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
        deps["postgres"] = "connected"
    except Exception as exc:  # noqa: BLE001
        deps["postgres"] = f"error: {exc}"

    # Redis (via Django cache backend)
    try:
        from django.core.cache import cache

        cache.set("__health__", "ok", 5)
        deps["redis"] = "connected" if cache.get("__health__") == "ok" else "error"
    except Exception as exc:  # noqa: BLE001
        deps["redis"] = f"error: {exc}"

    overall = "healthy" if all(v == "connected" for v in deps.values()) else "degraded"
    status_code = 200 if overall == "healthy" else 503
    return JsonResponse(
        {
            "status": overall,
            "service": "django",
            "version": "1.0.0",
            "dependencies": deps,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        },
        status=status_code,
    )
