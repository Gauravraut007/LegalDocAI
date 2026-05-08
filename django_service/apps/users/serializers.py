"""DRF serializers + custom JWT serializer carrying workspace claims."""
from __future__ import annotations

import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from django.db import transaction
from rest_framework import serializers
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer

logger = logging.getLogger(__name__)
User = get_user_model()


# ----------------------------------------------------------------- public ---
class UserPublicSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "email", "full_name", "avatar", "is_staff", "date_joined")
        read_only_fields = fields


class UserUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("full_name", "avatar")


class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=12, max_length=128)
    full_name = serializers.CharField(required=False, allow_blank=True, max_length=255)

    class Meta:
        model = User
        fields = ("email", "password", "full_name")

    def validate_password(self, value):
        validate_password(value)
        return value

    def validate_email(self, value):
        value = value.strip().lower()
        if User.objects.filter(email__iexact=value).exists():
            raise serializers.ValidationError("Email already registered")
        return value

    @transaction.atomic
    def create(self, validated_data):
        from apps.workspaces.models import Workspace, WorkspaceMember

        password = validated_data.pop("password")
        user = User.objects.create_user(password=password, **validated_data)

        # Personal default workspace
        ws = Workspace.objects.create(
            name=f"{user.display_name}'s Workspace",
            owner=user,
        )
        WorkspaceMember.objects.create(
            workspace=ws,
            user=user,
            role=WorkspaceMember.Role.OWNER,
        )
        return user


# ----------------------------------------------------------------- passwords -
class PasswordChangeSerializer(serializers.Serializer):
    current_password = serializers.CharField(write_only=True)
    new_password = serializers.CharField(write_only=True, min_length=12, max_length=128)

    def validate(self, attrs):
        user = self.context["request"].user
        if not user.check_password(attrs["current_password"]):
            raise serializers.ValidationError({"current_password": "incorrect password"})
        validate_password(attrs["new_password"], user=user)
        return attrs

    def save(self, **kwargs):
        user = self.context["request"].user
        user.set_password(self.validated_data["new_password"])
        user.save(update_fields=["password"])
        return user


class PasswordResetRequestSerializer(serializers.Serializer):
    email = serializers.EmailField()


class PasswordResetConfirmSerializer(serializers.Serializer):
    token = serializers.CharField()
    new_password = serializers.CharField(write_only=True, min_length=12, max_length=128)

    def validate_new_password(self, value):
        validate_password(value)
        return value


# --------------------------------------------------------------- JWT ---------
class LDIPTokenObtainPairSerializer(TokenObtainPairSerializer):
    """Add ``email``, ``workspace_ids``, ``is_staff`` claims to the access token."""

    @classmethod
    def get_token(cls, user):
        from apps.workspaces.models import WorkspaceMember

        token = super().get_token(user)
        token["email"] = user.email
        token["is_staff"] = bool(user.is_staff)
        ws_ids = list(
            WorkspaceMember.objects.filter(user=user).values_list("workspace_id", flat=True)
        )
        token["workspace_ids"] = [str(x) for x in ws_ids]
        return token
