"""Lightweight mirror of FastAPI's Document table.

The PK ``id`` mirrors the FastAPI document_id. Source-of-truth for OCR text,
chunks, vectors, etc. lives on the FastAPI side; we keep just enough metadata
to enforce workspace permissions and render listings without paying a
cross-service round-trip on every list call.
"""
from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models

from apps.workspaces.models import Workspace


class DocStatus(models.TextChoices):
    UPLOADED = "uploaded", "Uploaded"
    QUEUED = "queued", "Queued"
    PROCESSING = "processing", "Processing"
    READY = "ready", "Ready"
    FAILED = "failed", "Failed"


class DocType(models.TextChoices):
    UNKNOWN = "unknown", "Unknown"
    CONTRACT = "contract", "Contract"
    NDA = "nda", "NDA"
    COURT_FILING = "court_filing", "Court filing"
    GENERAL_LEGAL = "general_legal", "General legal"


class Document(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    workspace = models.ForeignKey(
        Workspace, on_delete=models.CASCADE, related_name="documents"
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="uploaded_documents",
    )
    title = models.CharField(max_length=500, blank=True, default="")
    original_filename = models.CharField(max_length=500)
    doc_type = models.CharField(
        max_length=32, choices=DocType.choices, default=DocType.UNKNOWN
    )
    status = models.CharField(
        max_length=32, choices=DocStatus.choices, default=DocStatus.UPLOADED
    )
    size_bytes = models.BigIntegerField(default=0)
    page_count = models.IntegerField(null=True, blank=True)
    sha256 = models.CharField(max_length=64, blank=True, default="", db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ldip_documents_mirror"
        ordering = ("-created_at",)
        indexes = [
            models.Index(fields=["workspace", "-created_at"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self) -> str:
        return self.title or self.original_filename
