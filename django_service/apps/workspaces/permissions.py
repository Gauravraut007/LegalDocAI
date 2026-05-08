"""DRF permission classes for workspace-scoped resources."""
from __future__ import annotations

from rest_framework.permissions import SAFE_METHODS, BasePermission

from .models import Workspace, WorkspaceMember


_WRITE_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN, WorkspaceMember.Role.MEMBER}
_ADMIN_ROLES = {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}


def _resolve_workspace(view, obj=None) -> Workspace | None:
    if obj is not None:
        ws = getattr(obj, "workspace", None)
        if isinstance(ws, Workspace):
            return ws
        if isinstance(obj, Workspace):
            return obj
    ws_id = view.kwargs.get("workspace_id") or view.kwargs.get("pk")
    if not ws_id:
        return None
    try:
        return Workspace.objects.get(pk=ws_id)
    except Workspace.DoesNotExist:
        return None


def _membership(user, workspace: Workspace | None) -> WorkspaceMember | None:
    if workspace is None or not user or not user.is_authenticated:
        return None
    return WorkspaceMember.objects.filter(workspace=workspace, user=user).first()


class IsWorkspaceMember(BasePermission):
    """User must be any kind of member; viewers are read-only."""

    message = "You are not a member of this workspace."

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        ws = _resolve_workspace(view)
        if ws is None:
            return True  # list/create view; defer to has_object_permission
        m = _membership(request.user, ws)
        if m is None:
            return request.user.is_staff
        if request.method in SAFE_METHODS:
            return True
        return m.role in _WRITE_ROLES

    def has_object_permission(self, request, view, obj):
        ws = _resolve_workspace(view, obj)
        m = _membership(request.user, ws)
        if m is None:
            return bool(request.user.is_staff)
        if request.method in SAFE_METHODS:
            return True
        return m.role in _WRITE_ROLES


class IsWorkspaceAdmin(BasePermission):
    message = "Workspace admin role required."

    def has_permission(self, request, view):
        ws = _resolve_workspace(view)
        if ws is None:
            return request.user and request.user.is_authenticated
        m = _membership(request.user, ws)
        if request.user.is_staff:
            return True
        return bool(m and m.role in _ADMIN_ROLES)

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        ws = _resolve_workspace(view, obj)
        m = _membership(request.user, ws)
        return bool(m and m.role in _ADMIN_ROLES)


class IsWorkspaceOwner(BasePermission):
    message = "Workspace owner role required."

    def has_permission(self, request, view):
        ws = _resolve_workspace(view)
        if ws is None:
            return request.user and request.user.is_authenticated
        if request.user.is_staff:
            return True
        return ws.owner_id == request.user.id

    def has_object_permission(self, request, view, obj):
        if request.user.is_staff:
            return True
        ws = _resolve_workspace(view, obj)
        return bool(ws and ws.owner_id == request.user.id)
