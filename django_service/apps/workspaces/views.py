"""Workspace, member, and invitation views."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from django.db import transaction
from django.shortcuts import get_object_or_404
from django.utils import timezone
from drf_spectacular.utils import OpenApiResponse, extend_schema
from rest_framework import generics, mixins, permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from . import serializers as _ser
from .models import Invitation, Workspace, WorkspaceMember
from .permissions import IsWorkspaceAdmin, IsWorkspaceMember, IsWorkspaceOwner

User = get_user_model()


@extend_schema(tags=["workspaces"])
class WorkspaceViewSet(viewsets.ModelViewSet):
    serializer_class = _ser.WorkspaceSerializer
    permission_classes = [permissions.IsAuthenticated]
    queryset = Workspace.objects.none()

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Workspace.objects.none()
        user = self.request.user
        if user.is_staff:
            return Workspace.objects.all().order_by("-created_at")
        return Workspace.objects.filter(members__user=user).distinct().order_by("-created_at")

    def get_serializer_class(self):
        if self.action == "create":
            return _ser.WorkspaceCreateSerializer
        return _ser.WorkspaceSerializer

    def get_permissions(self):
        if self.action in {"update", "partial_update", "destroy"}:
            return [permissions.IsAuthenticated(), IsWorkspaceOwner()]
        if self.action in {"retrieve"}:
            return [permissions.IsAuthenticated(), IsWorkspaceMember()]
        return [permissions.IsAuthenticated()]

    @transaction.atomic
    def perform_create(self, serializer):
        ws = serializer.save(owner=self.request.user)
        WorkspaceMember.objects.create(
            workspace=ws,
            user=self.request.user,
            role=WorkspaceMember.Role.OWNER,
        )

    def create(self, request, *args, **kwargs):
        ser = self.get_serializer(data=request.data)
        ser.is_valid(raise_exception=True)
        self.perform_create(ser)
        ws = ser.instance
        return Response(_ser.WorkspaceSerializer(ws).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["workspaces"])
class WorkspaceMembersView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceMember]

    @extend_schema(responses={200: _ser.WorkspaceMemberSerializer(many=True)})
    def get(self, request, workspace_id):
        ws = get_object_or_404(Workspace, pk=workspace_id)
        self.check_object_permissions(request, ws)
        members = ws.members.select_related("user").all()
        return Response(_ser.WorkspaceMemberSerializer(members, many=True).data)

    @extend_schema(request=_ser.WorkspaceMemberAddSerializer,
                   responses={201: _ser.WorkspaceMemberSerializer})
    def post(self, request, workspace_id):
        ws = get_object_or_404(Workspace, pk=workspace_id)
        # Adding requires admin
        admin = IsWorkspaceAdmin()
        if not admin.has_object_permission(request, self, ws):
            return Response({"detail": "admin role required"}, status=403)
        ser = _ser.WorkspaceMemberAddSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        try:
            user = User.objects.get(email__iexact=ser.validated_data["email"])
        except User.DoesNotExist:
            return Response({"detail": "no such user"}, status=404)
        member, created = WorkspaceMember.objects.get_or_create(
            workspace=ws, user=user,
            defaults={"role": ser.validated_data["role"]},
        )
        if not created:
            return Response({"detail": "user already a member"}, status=409)
        return Response(_ser.WorkspaceMemberSerializer(member).data, status=201)


@extend_schema(tags=["workspaces"])
class WorkspaceMemberDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def _resolve(self, request, workspace_id, member_id):
        ws = get_object_or_404(Workspace, pk=workspace_id)
        member = get_object_or_404(WorkspaceMember, pk=member_id, workspace=ws)
        return ws, member

    @extend_schema(request=_ser.WorkspaceMemberRoleSerializer,
                   responses={200: _ser.WorkspaceMemberSerializer})
    def patch(self, request, workspace_id, member_id):
        ws, member = self._resolve(request, workspace_id, member_id)
        if not (request.user.is_staff or ws.owner_id == request.user.id
                or WorkspaceMember.objects.filter(
                    workspace=ws, user=request.user,
                    role=WorkspaceMember.Role.ADMIN).exists()):
            return Response({"detail": "admin role required"}, status=403)
        ser = _ser.WorkspaceMemberRoleSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        new_role = ser.validated_data["role"]
        if member.role == WorkspaceMember.Role.OWNER and new_role != WorkspaceMember.Role.OWNER:
            return Response({"detail": "cannot demote workspace owner"}, status=400)
        member.role = new_role
        member.save(update_fields=["role"])
        return Response(_ser.WorkspaceMemberSerializer(member).data)

    @extend_schema(responses={204: OpenApiResponse(description="removed")})
    def delete(self, request, workspace_id, member_id):
        ws, member = self._resolve(request, workspace_id, member_id)
        is_self = member.user_id == request.user.id
        is_admin = (
            request.user.is_staff
            or ws.owner_id == request.user.id
            or WorkspaceMember.objects.filter(
                workspace=ws, user=request.user,
                role=WorkspaceMember.Role.ADMIN,
            ).exists()
        )
        if not (is_self or is_admin):
            return Response({"detail": "admin role required"}, status=403)
        if member.role == WorkspaceMember.Role.OWNER:
            return Response({"detail": "cannot remove workspace owner"}, status=400)
        member.delete()
        return Response(status=204)


@extend_schema(tags=["workspaces"])
class WorkspaceInvitationsView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceAdmin]

    @extend_schema(responses={200: _ser.InvitationSerializer(many=True)})
    def get(self, request, workspace_id):
        ws = get_object_or_404(Workspace, pk=workspace_id)
        self.check_object_permissions(request, ws)
        invs = ws.invitations.all()
        return Response(_ser.InvitationSerializer(invs, many=True).data)

    @extend_schema(request=_ser.InvitationCreateSerializer,
                   responses={201: _ser.InvitationSerializer})
    def post(self, request, workspace_id):
        ws = get_object_or_404(Workspace, pk=workspace_id)
        self.check_object_permissions(request, ws)
        ser = _ser.InvitationCreateSerializer(data=request.data)
        ser.is_valid(raise_exception=True)
        inv = Invitation.objects.create(
            workspace=ws,
            email=ser.validated_data["email"],
            role=ser.validated_data["role"],
            invited_by=request.user,
        )
        return Response(_ser.InvitationSerializer(inv).data, status=201)


@extend_schema(tags=["workspaces"])
class WorkspaceInvitationRevokeView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsWorkspaceAdmin]

    @extend_schema(responses={204: OpenApiResponse(description="revoked")})
    def delete(self, request, workspace_id, invitation_id):
        ws = get_object_or_404(Workspace, pk=workspace_id)
        self.check_object_permissions(request, ws)
        inv = get_object_or_404(Invitation, pk=invitation_id, workspace=ws)
        if inv.status == Invitation.Status.PENDING:
            inv.status = Invitation.Status.REVOKED
            inv.save(update_fields=["status"])
        return Response(status=204)


@extend_schema(tags=["workspaces"])
class InvitationAcceptView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    @extend_schema(
        request=None,
        responses={200: OpenApiResponse(description="accepted; refresh JWT to update workspace claims")},
    )
    @transaction.atomic
    def post(self, request, token):
        try:
            inv = Invitation.objects.select_for_update().get(token=token)
        except Invitation.DoesNotExist:
            return Response({"detail": "invalid token"}, status=404)
        if not inv.is_active():
            return Response({"detail": "invitation expired or already used"}, status=400)
        if inv.email.lower() != request.user.email.lower():
            return Response({"detail": "invitation email does not match user"}, status=403)

        member, created = WorkspaceMember.objects.get_or_create(
            workspace=inv.workspace, user=request.user,
            defaults={"role": inv.role},
        )
        inv.status = Invitation.Status.ACCEPTED
        inv.accepted_at = timezone.now()
        inv.save(update_fields=["status", "accepted_at"])
        return Response(
            {
                "workspace_id": str(inv.workspace_id),
                "role": member.role,
                "membership_id": str(member.id),
                "created": created,
                "detail": "Refresh your JWT to receive updated workspace_ids claim.",
            }
        )
