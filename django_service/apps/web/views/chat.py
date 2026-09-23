"""Chat UI: list, new session, session detail, streaming proxy."""
from __future__ import annotations

import json
import logging

import httpx
from asgiref.sync import sync_to_async
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpResponse, JsonResponse, StreamingHttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import csrf_protect

from apps.chat.models import ChatMessage, ChatSession
from apps.documents.models import Document
from apps.documents.services.fastapi_client import FastAPIClient, FastAPIClientError

from ..forms import ChatSessionForm
from ..utils import (
    get_active_workspace,
    list_user_workspaces,
    mint_access_token,
    user_is_workspace_member,
)

logger = logging.getLogger(__name__)


def _session_or_403(request, session_id) -> ChatSession:
    sess = get_object_or_404(ChatSession, pk=session_id)
    user = request.user
    if user.is_staff or sess.created_by_id == user.id:
        return sess
    if sess.workspace_id and user_is_workspace_member(user, sess.workspace):
        return sess
    from django.core.exceptions import PermissionDenied
    raise PermissionDenied("You don't have access to this chat session.")


def _doc_choices(active_ws):
    if active_ws is None:
        return []
    docs = Document.objects.filter(
        workspace=active_ws, status="ready",
    ).order_by("-created_at")
    return [(str(d.id), f"{d.title or d.original_filename} · {d.doc_type}") for d in docs]


@method_decorator(login_required, name="dispatch")
class ChatSessionListView(View):
    template_name = "web/chat/list.html"

    def get(self, request):
        active = get_active_workspace(request)
        if active is None:
            messages.info(request, "Create or join a workspace first.")
            return redirect("web:workspaces")
        sessions = ChatSession.objects.filter(workspace=active).order_by("-updated_at")[:200]
        return render(request, self.template_name, {
            "sessions": sessions,
            "active_workspace": active,
            "all_workspaces": list_user_workspaces(request.user),
        })


@method_decorator(login_required, name="dispatch")
class ChatNewView(View):
    template_name = "web/chat/new.html"

    def get(self, request):
        active = get_active_workspace(request)
        if active is None:
            return redirect("web:workspaces")
        choices = _doc_choices(active)
        if not choices:
            messages.warning(request, "No 'ready' documents yet. Upload and wait for processing.")
        return render(request, self.template_name, {
            "form": ChatSessionForm(document_choices=choices),
            "active_workspace": active,
        })

    def post(self, request):
        active = get_active_workspace(request)
        if active is None:
            return redirect("web:workspaces")
        choices = _doc_choices(active)
        form = ChatSessionForm(request.POST, document_choices=choices)
        if not form.is_valid():
            return render(request, self.template_name, {
                "form": form, "active_workspace": active,
            }, status=400)

        token = mint_access_token(request.user)
        payload = {
            "title": form.cleaned_data.get("title") or "",
            "document_ids": list(form.cleaned_data["document_ids"]),
            "workspace_id": str(active.id),
        }
        try:
            with httpx.Client(
                base_url=settings.FASTAPI_BASE_URL,
                timeout=httpx.Timeout(connect=10.0, read=30, write=30, pool=10.0),
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                r = client.post("/api/v1/chat/sessions", json=payload)
            if r.status_code >= 400:
                messages.error(request, f"Could not create session ({r.status_code}): {r.text}")
                return render(request, self.template_name, {
                    "form": form, "active_workspace": active,
                }, status=502)
            upstream = r.json()
        except httpx.HTTPError as exc:
            logger.exception("create_session_http_error")
            messages.error(request, f"Could not create session: {exc}")
            return render(request, self.template_name, {
                "form": form, "active_workspace": active,
            }, status=502)

        sess, _ = ChatSession.objects.update_or_create(
            id=upstream["id"],
            defaults={
                "workspace": active,
                "created_by": request.user,
                "title": (upstream.get("title") or payload["title"])[:500],
                "document_ids": upstream.get("document_ids", payload["document_ids"]),
            },
        )
        return redirect("web:chat_detail", pk=sess.id)


@method_decorator(login_required, name="dispatch")
class ChatSessionDetailView(View):
    template_name = "web/chat/detail.html"

    def get(self, request, pk):
        sess = _session_or_403(request, pk)
        # Pull authoritative transcript from upstream so we render the same
        # history both browsers see; persist a local mirror for convenience.
        token = mint_access_token(request.user)
        upstream_messages = []
        try:
            with httpx.Client(
                base_url=settings.FASTAPI_BASE_URL,
                timeout=httpx.Timeout(connect=10.0, read=30, write=30, pool=10.0),
                headers={"Authorization": f"Bearer {token}"},
            ) as client:
                r = client.get(f"/api/v1/chat/sessions/{sess.id}")
                if r.status_code == 200:
                    upstream_messages = r.json().get("messages", [])
        except httpx.HTTPError:
            logger.warning("session_history_pull_failed",
                           extra={"session_id": str(sess.id)})
        # Backing document titles for source rendering.
        doc_ids = [str(d) for d in (sess.document_ids or [])]
        docs = Document.objects.filter(id__in=doc_ids)
        docs_by_id = {str(d.id): d for d in docs}

        return render(request, self.template_name, {
            "session": sess,
            "chat_messages": upstream_messages,
            "documents": [docs_by_id.get(d) for d in doc_ids if d in docs_by_id],
            "stream_url": f"/web/chat/{sess.id}/stream",
        })


# ---------------------------------------------------------------- streaming --
@sync_to_async
def _check_session_access(user, session_id):
    try:
        sess = ChatSession.objects.select_related("workspace").get(pk=session_id)
    except ChatSession.DoesNotExist:
        return None
    if user.is_staff or sess.created_by_id == user.id:
        return sess
    if sess.workspace_id and user_is_workspace_member(user, sess.workspace):
        return sess
    return None


@csrf_protect
async def chat_message_stream(request, pk):
    """POST → SSE proxy of FastAPI's chat message stream."""
    if request.method != "POST":
        return HttpResponse(status=405)
    user = await request.auser()
    if not user.is_authenticated:
        return HttpResponse(status=401)
    sess = await _check_session_access(user, str(pk))
    if sess is None:
        return HttpResponse(status=404)

    body = request.body
    try:
        data = json.loads(body.decode("utf-8") or "{}")
    except json.JSONDecodeError:
        return JsonResponse({"detail": "invalid JSON"}, status=400)
    content = (data.get("content") or "").strip()
    if not content:
        return JsonResponse({"detail": "content required"}, status=400)

    token = await sync_to_async(mint_access_token)(user)

    async def proxy():
        try:
            async with FastAPIClient(token) as client:
                async for frame in client.stream_chat_message(str(pk), content):
                    event = frame.get("event") or "message"
                    payload = frame.get("data")
                    if not isinstance(payload, str):
                        payload = json.dumps(payload)
                    yield f"event: {event}\ndata: {payload}\n\n".encode("utf-8")
        except FastAPIClientError as exc:
            err = json.dumps({"detail": str(exc.detail), "status": exc.status_code})
            yield f"event: error\ndata: {err}\n\n".encode("utf-8")
        except Exception as exc:  # noqa: BLE001
            logger.exception("chat_proxy_error")
            yield f"event: error\ndata: {json.dumps({'detail': str(exc)})}\n\n".encode("utf-8")

    response = StreamingHttpResponse(proxy(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    response["X-Accel-Buffering"] = "no"
    return response
