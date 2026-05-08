"""Cross-cutting DRF permission classes (re-exported from apps.workspaces)."""
from __future__ import annotations

from apps.workspaces.permissions import (  # noqa: F401
    IsWorkspaceAdmin,
    IsWorkspaceMember,
    IsWorkspaceOwner,
)

__all__ = ["IsWorkspaceAdmin", "IsWorkspaceMember", "IsWorkspaceOwner"]
