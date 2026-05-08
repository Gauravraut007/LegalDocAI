"""Root URL config (Phase 4)."""
from __future__ import annotations

from django.contrib import admin
from django.shortcuts import redirect
from django.urls import include, path
from drf_spectacular.views import (
    SpectacularAPIView,
    SpectacularRedocView,
    SpectacularSwaggerView,
)

from core.health import healthz

api_v1 = [
    path("auth/", include(("apps.users.urls", "users"), namespace="users")),
    path("", include(("apps.workspaces.urls", "workspaces"), namespace="workspaces")),
    path("documents/", include(("apps.documents.urls", "documents"), namespace="documents")),
    path("chat/", include(("apps.chat.urls", "chat"), namespace="chat")),
]

urlpatterns = [
    path("admin/", admin.site.urls),
    path("web/", include(("apps.web.urls", "web"), namespace="web")),
    path("api/v1/", include((api_v1, "v1"), namespace="v1")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path(
        "api/docs/",
        SpectacularSwaggerView.as_view(url_name="schema"),
        name="swagger-ui",
    ),
    path(
        "api/redoc/",
        SpectacularRedocView.as_view(url_name="schema"),
        name="redoc",
    ),
    path("healthz/", healthz, name="healthz"),
    path("", lambda r: redirect("web:dashboard" if r.user.is_authenticated else "web:login")),
]
