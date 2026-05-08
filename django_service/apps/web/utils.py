"""Helpers shared by web views.

These functions hide the JWT/FastAPI bridging from templates and view code.
The user sits on a Django session; we mint short-lived JWTs server-side
on demand for each FastAPI call.
"""
from __future__ import annotations

import time
from typing import Any

from apps.users.serializers import LDIPTokenObtainPairSerializer
from apps.workspaces.models import Workspace, WorkspaceMember

# Per-process tiny TTL cache: avoid re-minting tokens on every page hit.
_TOKEN_TTL_SECONDS = 60
_token_cache: dict[str, tuple[str, float]] = {}


def mint_access_token(user) -> str:
    """Return a short-lived access JWT for ``user`` (cached for 60s)."""
    now = time.time()
    cached = _token_cache.get(str(user.pk))
    if cached and cached[1] > now:
        return cached[0]
    refresh = LDIPTokenObtainPairSerializer.get_token(user)
    token = str(refresh.access_token)
    _token_cache[str(user.pk)] = (token, now + _TOKEN_TTL_SECONDS)
    return token


# ---------------------------------------------------------------- workspace --
ACTIVE_WORKSPACE_SESSION_KEY = "active_workspace_id"


def list_user_workspaces(user) -> list[Workspace]:
    if user.is_staff:
        return list(Workspace.objects.all().order_by("-created_at"))
    return list(
        Workspace.objects.filter(members__user=user)
        .distinct()
        .order_by("-created_at")
    )


def get_active_workspace(request) -> Workspace | None:
    user = request.user
    if not user.is_authenticated:
        return None
    ws_id = request.session.get(ACTIVE_WORKSPACE_SESSION_KEY)
    if ws_id:
        try:
            ws = Workspace.objects.get(pk=ws_id)
        except Workspace.DoesNotExist:
            ws = None
        else:
            if user.is_staff or WorkspaceMember.objects.filter(
                workspace=ws, user=user
            ).exists():
                return ws
    # Fallback: first workspace the user belongs to.
    workspaces = list_user_workspaces(user)
    if not workspaces:
        return None
    set_active_workspace(request, workspaces[0])
    return workspaces[0]


def set_active_workspace(request, workspace: Workspace) -> None:
    request.session[ACTIVE_WORKSPACE_SESSION_KEY] = str(workspace.id)


def user_is_workspace_member(user, workspace: Workspace) -> bool:
    if not user.is_authenticated:
        return False
    if user.is_staff:
        return True
    return WorkspaceMember.objects.filter(
        workspace=workspace, user=user
    ).exists()


def user_role_in_workspace(user, workspace: Workspace) -> str | None:
    m = WorkspaceMember.objects.filter(workspace=workspace, user=user).first()
    return m.role if m else None
