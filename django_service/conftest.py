"""Shared pytest fixtures."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from apps.workspaces.models import Workspace, WorkspaceMember


@pytest.fixture
def api():
    return APIClient()


@pytest.fixture
def user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="alice@example.com",
        password="AlicePass123!Strong",
        full_name="Alice",
    )


@pytest.fixture
def other_user(db):
    User = get_user_model()
    return User.objects.create_user(
        email="bob@example.com",
        password="BobPass123!Strong",
        full_name="Bob",
    )


@pytest.fixture
def workspace(db, user):
    ws = Workspace.objects.create(name="Alice WS", owner=user)
    WorkspaceMember.objects.create(
        workspace=ws, user=user, role=WorkspaceMember.Role.OWNER,
    )
    return ws


@pytest.fixture
def auth_client(api, user):
    from rest_framework_simplejwt.tokens import RefreshToken
    from apps.users.serializers import LDIPTokenObtainPairSerializer

    refresh = LDIPTokenObtainPairSerializer.get_token(user)
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {refresh.access_token}")
    return api
