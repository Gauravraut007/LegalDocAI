"""Serializers for workspaces, members, invitations."""
from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import serializers

from .models import Invitation, Workspace, WorkspaceMember

User = get_user_model()


class WorkspaceSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ("id", "name", "slug", "owner", "plan", "created_at", "updated_at")
        read_only_fields = ("id", "slug", "owner", "created_at", "updated_at")


class WorkspaceCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Workspace
        fields = ("name", "plan")


class _MemberUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "full_name")


class WorkspaceMemberSerializer(serializers.ModelSerializer):
    user = _MemberUserSerializer(read_only=True)

    class Meta:
        model = WorkspaceMember
        fields = ("id", "workspace", "user", "role", "invited_at", "joined_at")
        read_only_fields = ("id", "workspace", "user", "invited_at", "joined_at")


class WorkspaceMemberAddSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=WorkspaceMember.Role.choices,
        default=WorkspaceMember.Role.MEMBER,
    )

    def validate_email(self, value):
        return value.strip().lower()


class WorkspaceMemberRoleSerializer(serializers.Serializer):
    role = serializers.ChoiceField(choices=WorkspaceMember.Role.choices)


class InvitationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Invitation
        fields = (
            "id", "workspace", "email", "role", "token", "status",
            "invited_by", "created_at", "expires_at", "accepted_at",
        )
        read_only_fields = (
            "id", "workspace", "token", "status", "invited_by",
            "created_at", "expires_at", "accepted_at",
        )


class InvitationCreateSerializer(serializers.Serializer):
    email = serializers.EmailField()
    role = serializers.ChoiceField(
        choices=WorkspaceMember.Role.choices,
        default=WorkspaceMember.Role.MEMBER,
    )

    def validate_email(self, value):
        return value.strip().lower()
