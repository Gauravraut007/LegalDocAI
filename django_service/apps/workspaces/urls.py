from django.urls import path
from rest_framework.routers import DefaultRouter

from . import views

app_name = "workspaces"

router = DefaultRouter()
router.register(r"workspaces", views.WorkspaceViewSet, basename="workspace")

urlpatterns = router.urls + [
    path("workspaces/<uuid:workspace_id>/members/",
         views.WorkspaceMembersView.as_view(), name="members"),
    path("workspaces/<uuid:workspace_id>/members/<uuid:member_id>/",
         views.WorkspaceMemberDetailView.as_view(), name="member-detail"),
    path("workspaces/<uuid:workspace_id>/invitations/",
         views.WorkspaceInvitationsView.as_view(), name="invitations"),
    path("workspaces/<uuid:workspace_id>/invitations/<uuid:invitation_id>/",
         views.WorkspaceInvitationRevokeView.as_view(), name="invitation-revoke"),
    path("invitations/accept/<str:token>/",
         views.InvitationAcceptView.as_view(), name="invitation-accept"),
]
