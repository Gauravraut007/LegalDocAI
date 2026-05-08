"""Dashboard + workspace switcher."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_POST

from apps.chat.models import ChatSession
from apps.documents.models import Document
from apps.workspaces.models import Workspace

from ..utils import (
    get_active_workspace,
    list_user_workspaces,
    set_active_workspace,
    user_is_workspace_member,
)


@method_decorator(login_required, name="dispatch")
class DashboardView(View):
    template_name = "web/dashboard.html"

    def get(self, request):
        active = get_active_workspace(request)
        ctx = {
            "active_workspace": active,
            "all_workspaces": list_user_workspaces(request.user),
            "recent_documents": [],
            "recent_sessions": [],
            "doc_counts": {"ready": 0, "processing": 0, "failed": 0, "total": 0},
        }
        if active:
            docs = Document.objects.filter(workspace=active).order_by("-created_at")
            ctx["recent_documents"] = list(docs[:8])
            counts = {"ready": 0, "processing": 0, "failed": 0, "total": 0}
            for d in docs:
                counts["total"] += 1
                if d.status == "ready":
                    counts["ready"] += 1
                elif d.status == "failed":
                    counts["failed"] += 1
                elif d.status in ("uploaded", "queued", "processing"):
                    counts["processing"] += 1
            ctx["doc_counts"] = counts
            ctx["recent_sessions"] = list(
                ChatSession.objects.filter(workspace=active)
                .order_by("-updated_at")[:6]
            )
        return render(request, self.template_name, ctx)


@require_POST
@login_required
def switch_workspace(request):
    ws_id = request.POST.get("workspace_id")
    next_url = request.POST.get("next") or "web:dashboard"
    if ws_id:
        ws = get_object_or_404(Workspace, pk=ws_id)
        if user_is_workspace_member(request.user, ws):
            set_active_workspace(request, ws)
            messages.success(request, f"Switched to {ws.name}.")
        else:
            messages.error(request, "You are not a member of that workspace.")
    if next_url.startswith("/"):
        return redirect(next_url)
    return redirect(next_url)
