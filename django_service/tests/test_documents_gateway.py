"""Document upload proxies to FastAPI; mirror row created."""
from __future__ import annotations

import io
import uuid
from unittest.mock import patch

import pytest
from django.urls import reverse


@pytest.mark.django_db
def test_upload_creates_mirror_row(auth_client, workspace):
    fake_doc_id = str(uuid.uuid4())
    upstream_payload = {
        "document_id": fake_doc_id,
        "ingest_job_id": str(uuid.uuid4()),
        "status": "uploaded",
        "deduplicated": False,
        "message": "ok",
        "original_filename": "demo.pdf",
        "size_bytes": 12,
        "sha256": "deadbeef",
    }

    async def fake_upload(self, **kwargs):
        return upstream_payload

    with patch(
        "apps.documents.services.fastapi_client.FastAPIClient.upload",
        new=fake_upload,
    ):
        resp = auth_client.post(
            reverse("v1:documents:list-create"),
            {
                "file": io.BytesIO(b"hello world!"),
                "workspace_id": str(workspace.id),
                "title": "Demo",
            },
            format="multipart",
        )
    assert resp.status_code == 202, resp.content
    body = resp.json()
    assert body["id"] == fake_doc_id
    assert body["workspace"] == str(workspace.id)

    # Mirror row exists
    from apps.documents.models import Document
    assert Document.objects.filter(id=fake_doc_id).exists()


@pytest.mark.django_db
def test_non_member_cannot_upload_to_workspace(auth_client, workspace, other_user, api):
    # Bob logs in
    login = api.post(
        reverse("v1:users:login"),
        {"email": other_user.email, "password": "BobPass123!Strong"},
        format="json",
    )
    api.credentials(HTTP_AUTHORIZATION=f"Bearer {login.json()['access']}")

    resp = api.post(
        reverse("v1:documents:list-create"),
        {
            "file": io.BytesIO(b"nope"),
            "workspace_id": str(workspace.id),
        },
        format="multipart",
    )
    assert resp.status_code == 403
