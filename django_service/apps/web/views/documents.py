"""Documents UI: list, upload, detail (live progress), delete, SSE proxy."""
from __future__ import annotations

import json
import logging

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import get_user
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View

from apps.documents.models import Document
from apps.documents.services.fastapi_client import FastAPIClient, FastAPIClientError

from ..forms import DocumentUploadForm
from ..utils import (
    get_active_workspace,
    list_user_workspaces,
    mint_access_token,
    user_is_workspace_member,
)

logger = logging.getLogger(__name__)


def _doc_or_403(request, pk) -> Document:
    doc = get_object_or_404(Document, pk=pk)
    if not user_is_workspace_member(request.user, doc.workspace):
        from django.core.exceptions import PermissionDenied
        raise PermissionDenied("You are not a member of this document's workspace.")
    return doc


@method_decorator(login_required, name="dispatch")
class DocumentListView(View):
    template_name = "web/documents/list.html"

    def get(self, request):
        active = get_active_workspace(request)
        if active is None:
            messages.info(request, "Create or join a workspace first.")
            return redirect("web:workspaces")
        status_filter = request.GET.get("status") or ""
        qs = Document.objects.filter(workspace=active).order_by("-created_at")
        if status_filter:
            qs = qs.filter(status=status_filter)
        return render(request, self.template_name, {
            "documents": qs[:200],
            "active_workspace": active,
            "all_workspaces": list_user_workspaces(request.user),
            "status_filter": status_filter,
            "status_choices": [
                ("", "All"),
                ("uploaded", "Uploaded"),
                ("queued", "Queued"),
                ("processing", "Processing"),
                ("ready", "Ready"),
                ("failed", "Failed"),
            ],
        })


@method_decorator(login_required, name="dispatch")
class DocumentUploadView(View):
    template_name = "web/documents/upload.html"

    def get(self, request):
        active = get_active_workspace(request)
        if active is None:
            messages.info(request, "Create or join a workspace first.")
            return redirect("web:workspaces")
        return render(request, self.template_name, {
            "form": DocumentUploadForm(),
            "active_workspace": active,
        })

    def post(self, request):
        active = get_active_workspace(request)
        if active is None:
            return redirect("web:workspaces")
        form = DocumentUploadForm(request.POST, request.FILES)
        if not form.is_valid():
            return render(request, self.template_name, {
                "form": form, "active_workspace": active,
            }, status=400)

        upload = form.cleaned_data["file"]
        title = (form.cleaned_data.get("title") or upload.name).strip()
        token = mint_access_token(request.user)

        try:
            with httpx.Client(
                base_url=settings.FASTAPI_BASE_URL,
                timeout=httpx.Timeout(connect=10.0, read=120, write=120, pool=10.0),
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                files = {
                    "file": (upload.name, upload.read(),
                             upload.content_type or "application/octet-stream"),
                }
                data = {"workspace_id": str(active.id)}
                resp = client.post("/api/v1/documents/upload",
                                   files=files, data=data)
                if resp.status_code >= 400:
                    detail = resp.text
                    try:
                        detail = resp.json().get("detail", detail)
                    except Exception:  # noqa: BLE001
                        pass
                    messages.error(request, f"Upload failed ({resp.status_code}): {detail}")
                    return render(request, self.template_name, {
                        "form": form, "active_workspace": active,
                    }, status=502)
                payload = resp.json()
        except httpx.HTTPError as exc:
            logger.exception("upload_http_error")
            messages.error(request, f"Upload failed: {exc}")
            return render(request, self.template_name, {
                "form": form, "active_workspace": active,
            }, status=502)

        doc, _ = Document.objects.update_or_create(
            id=payload["document_id"],
            defaults={
                "workspace": active,
                "uploaded_by": request.user,
                "title": title[:500],
                "original_filename": payload.get("original_filename") or upload.name,
                "size_bytes": int(payload.get("size_bytes", 0)),
                "sha256": payload.get("sha256", ""),
                "status": payload.get("status", "uploaded"),
            },
        )
        if payload.get("deduplicated"):
            messages.info(request, "Identical document already exists; reusing it.")
        else:
            messages.success(request, "Upload accepted; processing started.")
        return redirect("web:document_detail", pk=doc.id)


@method_decorator(login_required, name="dispatch")
class DocumentDetailView(View):
    template_name = "web/documents/detail.html"

    def get(self, request, pk):
        doc = _doc_or_403(request, pk)
        # Lazy-pull upstream snapshot to refresh local mirror.
        token = mint_access_token(request.user)
        upstream = None
        try:
            with httpx.Client(
                base_url=settings.FASTAPI_BASE_URL,
                timeout=httpx.Timeout(connect=10.0, read=30, write=30, pool=10.0),
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                r = client.get(f"/api/v1/documents/{doc.id}")
                if r.status_code == 200:
                    upstream = r.json()
                    updates = {}
                    if upstream.get("status") and upstream["status"] != doc.status:
                        updates["status"] = upstream["status"]
                    if upstream.get("doc_type") and upstream["doc_type"] != doc.doc_type:
                        updates["doc_type"] = upstream["doc_type"]
                    if upstream.get("page_count") and upstream["page_count"] != doc.page_count:
                        updates["page_count"] = upstream["page_count"]
                    if updates:
                        for k, v in updates.items():
                            setattr(doc, k, v)
                        doc.save(update_fields=list(updates.keys()) + ["updated_at"])
        except httpx.HTTPError:
            logger.warning("upstream_pull_failed", extra={"doc_id": str(doc.id)})

        is_terminal = doc.status in ("ready", "failed")
        return render(request, self.template_name, {
            "doc": doc,
            "upstream": upstream,
            "is_terminal": is_terminal,
        })


@login_required
def document_delete(request, pk):
    if request.method != "POST":
        return HttpResponse(status=405)
    doc = _doc_or_403(request, pk)
    token = mint_access_token(request.user)

    try:
        with httpx.Client(
            base_url=settings.FASTAPI_BASE_URL,
            timeout=httpx.Timeout(connect=10.0, read=30, write=30, pool=10.0),
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            r = client.delete(f"/api/v1/documents/{doc.id}")
            if r.status_code not in (204, 404):
                messages.error(request, f"Delete failed ({r.status_code}).")
                return redirect("web:document_detail", pk=doc.id)
    except httpx.HTTPError as exc:
        logger.exception("delete_http_error")
        messages.error(request, f"Delete failed: {exc}")
        return redirect("web:document_detail", pk=doc.id)

    doc.delete()
    messages.success(request, "Document deleted.")
    return redirect("web:documents")


# ---------------------------------------------------------------- SSE proxy --
@sync_to_async
def _check_doc_access(user, document_id):
    try:
        doc = Document.objects.select_related("workspace").get(pk=document_id)
    except Document.DoesNotExist:
        return None
    if user_is_workspace_member(user, doc.workspace):
        return doc
    return None


async def document_events_stream(request, pk):
    """Async SSE proxy: ``/web/documents/<id>/events`` → FastAPI SSE."""
    user = await sync_to_async(get_user, thread_sensitive=True)(request)
    if not user.is_authenticated:
        return HttpResponse(status=401)
    doc = await _check_doc_access(user, str(pk))
    if doc is None:
        return HttpResponse(status=404)

    token = await sync_to_async(mint_access_token, thread_sensitive=True)(user)

    async def proxy():
        try:
            async with FastAPIClient(token) as client:
                async for frame in client.stream_events(str(pk)):
                    event = frame.get("event") or "message"
                    data = frame.get("data")
                    if not isinstance(data, str):
                        data = json.dumps(data)
                    yield f"event: {event}\ndata: {data}\n\n".encode("utf-8")
                    if event == "end":
                        break
        except FastAPIClientError as exc:
            payload = json.dumps({"detail": str(exc.detail), "status": exc.status_code})
            yield f"event: error\ndata: {payload}\n\n".encode("utf-8")

    response = StreamingHttpResponse(proxy(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
