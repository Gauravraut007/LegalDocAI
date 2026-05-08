"""Re-export view callables for the URLconf."""
from .auth import (
    LoginView,
    RegisterView,
    LogoutView,
    PasswordChangeView,
    ProfileView,
)
from .chat import (
    ChatNewView,
    ChatSessionDetailView,
    ChatSessionListView,
    chat_message_stream,
)
from .dashboard import DashboardView, switch_workspace
from .documents import (
    DocumentDetailView,
    DocumentListView,
    DocumentUploadView,
    document_delete,
    document_events_stream,
)
from .workspaces import (
    InvitationAcceptView,
    WorkspaceDetailView,
    WorkspaceInvitationsView,
    WorkspaceListView,
    WorkspaceMembersView,
    WorkspaceNewView,
)

__all__ = [
    "LoginView",
    "RegisterView",
    "LogoutView",
    "PasswordChangeView",
    "ProfileView",
    "DashboardView",
    "switch_workspace",
    "WorkspaceListView",
    "WorkspaceNewView",
    "WorkspaceDetailView",
    "WorkspaceMembersView",
    "WorkspaceInvitationsView",
    "InvitationAcceptView",
    "DocumentListView",
    "DocumentUploadView",
    "DocumentDetailView",
    "document_delete",
    "document_events_stream",
    "ChatSessionListView",
    "ChatNewView",
    "ChatSessionDetailView",
    "chat_message_stream",
]
