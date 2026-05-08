"""Workspace, membership, and invitation models."""
from __future__ import annotations

import secrets
import uuid
from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone
from django.utils.text import slugify


class Workspace(models.Model):
    class Plan(models.TextChoices):
        FREE = "free", "Free"
        PRO = "pro", "Pro"
        ENTERPRISE = "enterprise", "Enterprise"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_workspaces",
    )
    plan = models.CharField(max_length=20, choices=Plan.choices, default=Plan.FREE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ldip_workspaces"
        ordering = ("-created_at",)

    def save(self, *args, **kwargs):
        if not self.slug:
            base = slugify(self.name)[:200] or "workspace"
            slug = base
            i = 1
            while Workspace.objects.filter(slug=slug).exclude(pk=self.pk).exists():
                i += 1
                slug = f"{base}-{i}"
            self.slug = slug
        super().save(*args, **kwargs)

    def __str__(self) -> str:
        return self.name


class WorkspaceMember(models.Model):
    class Role(models.TextChoices):
        OWNER = "owner", "Owner"
        ADMIN = "admin", "Admin"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="members"
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="workspace_memberships",
    )
    role = models.CharField(max_length=20, choices=Role.choices, default=Role.MEMBER)
    invited_at = models.DateTimeField(default=timezone.now)
    joined_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "ldip_workspace_members"
        unique_together = (("workspace", "user"),)
        indexes = [
            models.Index(fields=["user"]),
            models.Index(fields=["workspace"]),
        ]

    def __str__(self) -> str:
        return f"{self.user_id} @ {self.workspace_id} ({self.role})"


def _default_token() -> str:
    return secrets.token_urlsafe(32)


def _default_expiry():
    return timezone.now() + timedelta(days=7)


class Invitation(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        ACCEPTED = "accepted", "Accepted"
        REVOKED = "revoked", "Revoked"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="invitations"
    )
    email = models.EmailField()
    role = models.CharField(
        max_length=20,
        choices=WorkspaceMember.Role.choices,
        default=WorkspaceMember.Role.MEMBER,
    )
    token = models.CharField(max_length=64, unique=True, default=_default_token)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.PENDING
    )
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="sent_invitations",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=_default_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "ldip_invitations"
        ordering = ("-created_at",)
        indexes = [models.Index(fields=["workspace", "email"])]

    def is_active(self) -> bool:
        return self.status == self.Status.PENDING and self.expires_at > timezone.now()
