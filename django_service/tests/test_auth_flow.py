"""Auth flow: register → login → me → refresh → logout (blacklist)."""
from __future__ import annotations

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_register_creates_user_and_default_workspace(api):
    resp = api.post(
        reverse("v1:users:register"),
        {"email": "carol@example.com", "password": "CarolPass123!Strong",
         "full_name": "Carol"},
        format="json",
    )
    assert resp.status_code == 201, resp.content
    body = resp.json()
    assert body["email"] == "carol@example.com"

    # default workspace exists with the user as owner
    from apps.workspaces.models import Workspace, WorkspaceMember

    ws = Workspace.objects.get(owner__email="carol@example.com")
    assert WorkspaceMember.objects.filter(
        workspace=ws, user__email="carol@example.com",
        role=WorkspaceMember.Role.OWNER,
    ).exists()


@pytest.mark.django_db
def test_login_returns_jwt_with_workspace_claims(api, user, workspace):
    resp = api.post(
        reverse("v1:users:login"),
        {"email": user.email, "password": "AlicePass123!Strong"},
        format="json",
    )
    assert resp.status_code == 200, resp.content
    access = resp.json()["access"]

    import jwt

    from django.conf import settings
    payload = jwt.decode(
        access,
        settings.SIMPLE_JWT["SIGNING_KEY"],
        algorithms=[settings.SIMPLE_JWT["ALGORITHM"]],
        issuer="django-service",
    )
    assert payload["email"] == user.email
    assert payload["is_staff"] is False
    assert str(workspace.id) in payload["workspace_ids"]


@pytest.mark.django_db
def test_logout_blacklists_refresh(api, user):
    login = api.post(
        reverse("v1:users:login"),
        {"email": user.email, "password": "AlicePass123!Strong"},
        format="json",
    )
    refresh = login.json()["refresh"]
    access = login.json()["access"]
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {access}")

    out = api.post(reverse("v1:users:logout"), {"refresh": refresh}, format="json")
    assert out.status_code == 205

    again = api.post(reverse("v1:users:token-refresh"), {"refresh": refresh}, format="json")
    assert again.status_code == 401
