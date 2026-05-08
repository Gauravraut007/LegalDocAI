"""Context processors injected on every render."""
from __future__ import annotations

from .utils import get_active_workspace, list_user_workspaces


def workspace_context(request):
    """Expose ``active_workspace`` and ``user_workspaces`` to all templates."""
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {"active_workspace": None, "user_workspaces": []}
    return {
        "active_workspace": get_active_workspace(request),
        "user_workspaces": list_user_workspaces(request.user),
    }
