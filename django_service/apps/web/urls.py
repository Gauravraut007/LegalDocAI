"""Web URLconf."""
from __future__ import annotations

from django.urls import path

from . import views

app_name = "web"

urlpatterns = [
    # Auth
    path("login/", views.LoginView.as_view(), name="login"),
    path("register/", views.RegisterView.as_view(), name="register"),
    path("logout/", views.LogoutView.as_view(), name="logout"),
    path("profile/", views.ProfileView.as_view(), name="profile"),
    path("profile/password/", views.PasswordChangeView.as_view(), name="password_change"),

    # Dashboard + workspace switcher
    path("", views.DashboardView.as_view(), name="dashboard"),
    path("workspace/switch/", views.switch_workspace, name="workspace_switch"),

    # Workspaces
    path("workspaces/", views.WorkspaceListView.as_view(), name="workspaces"),
    path("workspaces/new/", views.WorkspaceNewView.as_view(), name="workspace_new"),
    path("workspaces/<uuid:pk>/", views.WorkspaceDetailView.as_view(), name="workspace_detail"),
    path("workspaces/<uuid:pk>/members/", views.WorkspaceMembersView.as_view(),
         name="workspace_members"),
    path("workspaces/<uuid:pk>/invitations/", views.WorkspaceInvitationsView.as_view(),
         name="workspace_invitations"),
    path("invitations/accept/", views.InvitationAcceptView.as_view(), name="invitation_accept"),
    path("invitations/accept/<str:token>/", views.InvitationAcceptView.as_view(),
         name="invitation_accept_token"),

    # Documents
    path("documents/", views.DocumentListView.as_view(), name="documents"),
    path("documents/upload/", views.DocumentUploadView.as_view(), name="document_upload"),
    path("documents/<uuid:pk>/", views.DocumentDetailView.as_view(), name="document_detail"),
    path("documents/<uuid:pk>/delete/", views.document_delete, name="document_delete"),
    path("documents/<uuid:pk>/events", views.document_events_stream, name="document_events"),

    # Chat
    path("chat/", views.ChatSessionListView.as_view(), name="chat_sessions"),
    path("chat/new/", views.ChatNewView.as_view(), name="chat_new"),
    path("chat/<uuid:pk>/", views.ChatSessionDetailView.as_view(), name="chat_detail"),
    path("chat/<uuid:pk>/stream", views.chat_message_stream, name="chat_stream"),
]
