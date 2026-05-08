from django.contrib import admin

from .models import ChatMessage, ChatSession


@admin.register(ChatSession)
class ChatSessionAdmin(admin.ModelAdmin):
    list_display = ("title", "created_by", "workspace", "updated_at", "created_at")
    search_fields = ("title", "created_by__email", "workspace__name")
    readonly_fields = ("id", "created_at", "updated_at")


@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    list_display = ("session", "role", "tokens_in", "tokens_out", "latency_ms",
                    "model_name", "created_at")
    list_filter = ("role",)
    search_fields = ("session__id", "content")
    readonly_fields = ("id", "created_at")
