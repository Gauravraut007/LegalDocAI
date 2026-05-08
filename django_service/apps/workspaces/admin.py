from django.contrib import admin

from .models import Invitation, Workspace, WorkspaceMember


@admin.register(Workspace)
class WorkspaceAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "owner", "plan", "created_at")
    list_filter = ("plan",)
    search_fields = ("name", "slug", "owner__email")
    readonly_fields = ("id", "slug", "created_at", "updated_at")


@admin.register(WorkspaceMember)
class WorkspaceMemberAdmin(admin.ModelAdmin):
    list_display = ("workspace", "user", "role", "joined_at")
    list_filter = ("role",)
    search_fields = ("workspace__name", "user__email")
    readonly_fields = ("id", "invited_at", "joined_at")


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("workspace", "email", "role", "status", "expires_at", "created_at")
    list_filter = ("status", "role")
    search_fields = ("workspace__name", "email")
    readonly_fields = ("id", "token", "created_at", "accepted_at")
