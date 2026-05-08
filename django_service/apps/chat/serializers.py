"""Chat serializers."""
from __future__ import annotations

from rest_framework import serializers

from .models import ChatMessage, ChatSession


class ChatMessageSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatMessage
        fields = (
            "id", "session", "role", "content", "sources",
            "tokens_in", "tokens_out", "latency_ms", "model_name", "created_at",
        )
        read_only_fields = fields


class ChatSessionSerializer(serializers.ModelSerializer):
    class Meta:
        model = ChatSession
        fields = (
            "id", "workspace", "created_by", "title", "document_ids",
            "created_at", "updated_at",
        )
        read_only_fields = ("id", "created_by", "created_at", "updated_at")


class ChatSessionDetailSerializer(ChatSessionSerializer):
    messages = ChatMessageSerializer(many=True, read_only=True)

    class Meta(ChatSessionSerializer.Meta):
        fields = ChatSessionSerializer.Meta.fields + ("messages",)


class ChatSessionCreateSerializer(serializers.Serializer):
    title = serializers.CharField(required=False, allow_blank=True, max_length=500)
    document_ids = serializers.ListField(
        child=serializers.UUIDField(), min_length=1, max_length=10,
    )
    workspace_id = serializers.UUIDField(required=False, allow_null=True)
