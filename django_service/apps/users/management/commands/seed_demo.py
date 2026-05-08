"""Seed a demo user, workspace, and sample document upload (best-effort)."""
from __future__ import annotations

import os

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.workspaces.models import Workspace, WorkspaceMember

User = get_user_model()

DEMO_EMAIL = os.environ.get("DEMO_EMAIL", "demo@ldip.local")
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "DemoPass123!Strong")


class Command(BaseCommand):
    help = "Create a demo user + workspace; print credentials."

    @transaction.atomic
    def handle(self, *args, **options):
        user, created = User.objects.get_or_create(
            email=DEMO_EMAIL,
            defaults={"full_name": "Demo User", "is_active": True},
        )
        if created or not user.has_usable_password():
            user.set_password(DEMO_PASSWORD)
            user.save()
        ws, _ = Workspace.objects.get_or_create(
            owner=user,
            name="Demo Workspace",
            defaults={"plan": Workspace.Plan.PRO},
        )
        WorkspaceMember.objects.get_or_create(
            workspace=ws, user=user,
            defaults={"role": WorkspaceMember.Role.OWNER},
        )
        self.stdout.write(self.style.SUCCESS(
            f"Demo ready:\n  email   : {DEMO_EMAIL}\n"
            f"  password: {DEMO_PASSWORD}\n"
            f"  workspace: {ws.id} ({ws.name})"
        ))
