"""Workspace permission semantics: viewer cannot delete, non-member 403."""
from __future__ import annotations

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from apps.workspaces.models import WorkspaceMember


@pytest.fixture
def viewer(db, workspace):
    User = get_user_model()
    u = User.objects.create_user(
        email="vince@example.com", password="VincePass123!Strong",
    )
    WorkspaceMember.objects.create(
        workspace=workspace, user=u, role=WorkspaceMember.Role.VIEWER,
    )
    return u


def _bearer_for(api, user, password):
    api2 = api.__class__()
    login = api2.post(
        reverse("v1:users:login"),
        {"email": user.email, "password": password},
        format="json",
    )
    api2.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")
    return api2


@pytest.mark.django_db
def test_non_member_cannot_view_workspace(api, other_user, workspace):
    client = _bearer_for(api, other_user, "BobPass123!Strong")
    resp = client.get(reverse("v1:workspaces:workspace-detail", args=[workspace.id]))
    assert resp.status_code in (403, 404)


@pytest.mark.django_db
def test_viewer_cannot_delete_workspace(api, viewer, workspace):
    client = _bearer_for(api, viewer, "VincePass123!Strong")
    resp = client.delete(reverse("v1:workspaces:workspace-detail", args=[workspace.id]))
    assert resp.status_code == 403


@pytest.mark.django_db
def test_owner_can_list_members(auth_client, workspace):
    resp = auth_client.get(reverse("v1:workspaces:members", args=[workspace.id]))
    assert resp.status_code == 200
    assert len(resp.json()) >= 1
