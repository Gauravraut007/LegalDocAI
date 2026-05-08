"""Workspace pages: list, create, detail, members, invitations, accept."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views import View

from apps.workspaces.models import Invitation, Workspace, WorkspaceMember

from ..forms import InvitationForm, WorkspaceForm
from ..utils import (
    list_user_workspaces,
    set_active_workspace,
    user_is_workspace_member,
    user_role_in_workspace,
)

User = get_user_model()


def _is_admin(user, ws: Workspace) -> bool:
    if user.is_staff or ws.owner_id == user.id:
        return True
    role = user_role_in_workspace(user, ws)
    return role in {WorkspaceMember.Role.OWNER, WorkspaceMember.Role.ADMIN}


@method_decorator(login_required, name="dispatch")
class WorkspaceListView(View):
    template_name = "web/workspaces/list.html"

    def get(self, request):
        return render(request, self.template_name, {
            "workspaces": list_user_workspaces(request.user),
        })


@method_decorator(login_required, name="dispatch")
class WorkspaceNewView(View):
    template_name = "web/workspaces/new.html"

    def get(self, request):
        return render(request, self.template_name, {"form": WorkspaceForm(initial={"plan": "free"})})

    @transaction.atomic
    def post(self, request):
        form = WorkspaceForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form}, status=400)
        ws = Workspace.objects.create(
            name=form.cleaned_data["name"],
            plan=form.cleaned_data["plan"],
            owner=request.user,
        )
        WorkspaceMember.objects.create(
            workspace=ws, user=request.user, role=WorkspaceMember.Role.OWNER,
        )
        set_active_workspace(request, ws)
        messages.success(request, f"Workspace '{ws.name}' created.")
        return redirect("web:workspace_detail", pk=ws.id)


@method_decorator(login_required, name="dispatch")
class WorkspaceDetailView(View):
    template_name = "web/workspaces/detail.html"

    def get(self, request, pk):
        ws = get_object_or_404(Workspace, pk=pk)
        if not user_is_workspace_member(request.user, ws):
            messages.error(request, "Not a member of that workspace.")
            return redirect("web:workspaces")
        members = ws.members.select_related("user").all()
        invitations = ws.invitations.filter(status=Invitation.Status.PENDING)
        return render(request, self.template_name, {
            "workspace": ws,
            "members": members,
            "invitations": invitations,
            "is_admin": _is_admin(request.user, ws),
            "is_owner": ws.owner_id == request.user.id or request.user.is_staff,
            "role": user_role_in_workspace(request.user, ws),
        })


@method_decorator(login_required, name="dispatch")
class WorkspaceMembersView(View):
    """POST add/remove/role-change handler — used from the detail page."""

    def post(self, request, pk):
        ws = get_object_or_404(Workspace, pk=pk)
        if not _is_admin(request.user, ws):
            messages.error(request, "Admin role required.")
            return redirect("web:workspace_detail", pk=ws.id)

        action = request.POST.get("action")
        if action == "add":
            email = (request.POST.get("email") or "").strip().lower()
            role = request.POST.get("role") or WorkspaceMember.Role.MEMBER
            try:
                user = User.objects.get(email__iexact=email)
            except User.DoesNotExist:
                messages.error(request, f"No user with email {email}.")
                return redirect("web:workspace_detail", pk=ws.id)
            _, created = WorkspaceMember.objects.get_or_create(
                workspace=ws, user=user, defaults={"role": role},
            )
            messages.success(
                request,
                f"Added {email} as {role}." if created else f"{email} is already a member.",
            )
        elif action == "remove":
            mid = request.POST.get("member_id")
            m = get_object_or_404(WorkspaceMember, pk=mid, workspace=ws)
            if m.role == WorkspaceMember.Role.OWNER:
                messages.error(request, "Cannot remove the workspace owner.")
            else:
                m.delete()
                messages.success(request, "Member removed.")
        elif action == "role":
            mid = request.POST.get("member_id")
            new_role = request.POST.get("role")
            m = get_object_or_404(WorkspaceMember, pk=mid, workspace=ws)
            if m.role == WorkspaceMember.Role.OWNER and new_role != WorkspaceMember.Role.OWNER:
                messages.error(request, "Cannot demote the workspace owner.")
            elif new_role in dict(WorkspaceMember.Role.choices):
                m.role = new_role
                m.save(update_fields=["role"])
                messages.success(request, "Role updated.")
        return redirect("web:workspace_detail", pk=ws.id)


@method_decorator(login_required, name="dispatch")
class WorkspaceInvitationsView(View):
    """POST invitation create / revoke."""

    def post(self, request, pk):
        ws = get_object_or_404(Workspace, pk=pk)
        if not _is_admin(request.user, ws):
            messages.error(request, "Admin role required.")
            return redirect("web:workspace_detail", pk=ws.id)
        action = request.POST.get("action")
        if action == "create":
            form = InvitationForm(request.POST)
            if form.is_valid():
                inv = Invitation.objects.create(
                    workspace=ws,
                    email=form.cleaned_data["email"],
                    role=form.cleaned_data["role"],
                    invited_by=request.user,
                )
                messages.success(
                    request,
                    f"Invitation token: {inv.token} (share with the invitee).",
                )
            else:
                messages.error(request, "Invalid invitation form.")
        elif action == "revoke":
            iid = request.POST.get("invitation_id")
            inv = get_object_or_404(Invitation, pk=iid, workspace=ws)
            if inv.status == Invitation.Status.PENDING:
                inv.status = Invitation.Status.REVOKED
                inv.save(update_fields=["status"])
                messages.success(request, "Invitation revoked.")
        return redirect("web:workspace_detail", pk=ws.id)


@method_decorator(login_required, name="dispatch")
class InvitationAcceptView(View):
    """User pastes / clicks an invitation token to join a workspace."""

    template_name = "web/workspaces/accept.html"

    def get(self, request, token=None):
        return render(request, self.template_name, {"token": token or ""})

    @transaction.atomic
    def post(self, request, token=None):
        token = (request.POST.get("token") or token or "").strip()
        try:
            inv = Invitation.objects.select_for_update().get(token=token)
        except Invitation.DoesNotExist:
            messages.error(request, "Invalid invitation token.")
            return render(request, self.template_name, {"token": token}, status=404)
        if not inv.is_active():
            messages.error(request, "Invitation expired or already used.")
            return render(request, self.template_name, {"token": token}, status=400)
        if inv.email.lower() != request.user.email.lower():
            messages.error(request, "Invitation email does not match your account.")
            return render(request, self.template_name, {"token": token}, status=403)
        WorkspaceMember.objects.get_or_create(
            workspace=inv.workspace, user=request.user,
            defaults={"role": inv.role},
        )
        inv.status = Invitation.Status.ACCEPTED
        inv.accepted_at = timezone.now()
        inv.save(update_fields=["status", "accepted_at"])
        set_active_workspace(request, inv.workspace)
        messages.success(request, f"Joined {inv.workspace.name} as {inv.role}.")
        return redirect("web:workspace_detail", pk=inv.workspace.id)
