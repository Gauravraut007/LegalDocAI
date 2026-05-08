"""Documents serializers (mirror models)."""
from __future__ import annotations

from rest_framework import serializers

from .models import Document


class DocumentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Document
        fields = (
            "id", "workspace", "uploaded_by", "title", "original_filename",
            "doc_type", "status", "size_bytes", "page_count", "sha256",
            "created_at", "updated_at",
        )
        read_only_fields = fields


class DocumentUploadSerializer(serializers.Serializer):
    file = serializers.FileField()
    workspace_id = serializers.UUIDField()
    title = serializers.CharField(required=False, allow_blank=True, max_length=500)
