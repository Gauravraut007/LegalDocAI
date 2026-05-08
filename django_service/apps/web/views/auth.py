"""Auth, registration, profile, password change."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth import authenticate, get_user_model, login, logout
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import HttpResponseRedirect
from django.shortcuts import redirect, render
from django.urls import reverse_lazy
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.http import require_http_methods

from apps.workspaces.models import Workspace, WorkspaceMember

from ..forms import EmailLoginForm, PasswordChangeForm, ProfileForm, RegisterForm

User = get_user_model()


def _client_ip(request) -> str | None:
    xff = request.META.get("HTTP_X_FORWARDED_FOR")
    if xff:
        return xff.split(",")[0].strip()
    return request.META.get("REMOTE_ADDR")


class LoginView(View):
    template_name = "web/auth/login.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("web:dashboard")
        return render(request, self.template_name, {"form": EmailLoginForm(request)})

    def post(self, request):
        form = EmailLoginForm(request, data=request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form}, status=400)
        user = form.get_user()
        login(request, user)
        user.last_login_ip = _client_ip(request)
        user.save(update_fields=["last_login_ip", "last_login"])
        messages.success(request, f"Welcome back, {user.display_name}.")
        next_url = request.GET.get("next") or request.POST.get("next") \
                   or reverse_lazy("web:dashboard")
        return HttpResponseRedirect(str(next_url))


class RegisterView(View):
    template_name = "web/auth/register.html"

    def get(self, request):
        if request.user.is_authenticated:
            return redirect("web:dashboard")
        return render(request, self.template_name, {"form": RegisterForm()})

    @transaction.atomic
    def post(self, request):
        form = RegisterForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form}, status=400)
        user = User.objects.create_user(
            email=form.cleaned_data["email"],
            password=form.cleaned_data["password"],
            full_name=form.cleaned_data.get("full_name") or "",
        )
        ws = Workspace.objects.create(
            name=f"{user.display_name}'s Workspace",
            owner=user,
        )
        WorkspaceMember.objects.create(
            workspace=ws, user=user, role=WorkspaceMember.Role.OWNER,
        )
        login(request, user)
        messages.success(request, "Account created. Welcome!")
        return redirect("web:dashboard")


@method_decorator(require_http_methods(["POST"]), name="dispatch")
class LogoutView(View):
    def post(self, request):
        logout(request)
        messages.info(request, "Signed out.")
        return redirect("web:login")


@method_decorator(login_required, name="dispatch")
class ProfileView(View):
    template_name = "web/profile.html"

    def get(self, request):
        form = ProfileForm(initial={
            "full_name": request.user.full_name,
            "avatar": request.user.avatar,
        })
        return render(request, self.template_name, {"form": form})

    def post(self, request):
        form = ProfileForm(request.POST)
        if not form.is_valid():
            return render(request, self.template_name, {"form": form}, status=400)
        request.user.full_name = form.cleaned_data.get("full_name") or ""
        request.user.avatar = form.cleaned_data.get("avatar") or ""
        request.user.save(update_fields=["full_name", "avatar"])
        messages.success(request, "Profile updated.")
        return redirect("web:profile")


@method_decorator(login_required, name="dispatch")
class PasswordChangeView(View):
    template_name = "web/auth/password_change.html"

    def get(self, request):
        return render(request, self.template_name,
                      {"form": PasswordChangeForm(user=request.user)})

    def post(self, request):
        form = PasswordChangeForm(request.POST, user=request.user)
        if not form.is_valid():
            return render(request, self.template_name,
                          {"form": form}, status=400)
        request.user.set_password(form.cleaned_data["new_password"])
        request.user.save(update_fields=["password"])
        # keep session alive with new password
        from django.contrib.auth import update_session_auth_hash
        update_session_auth_hash(request, request.user)
        messages.success(request, "Password changed.")
        return redirect("web:profile")
