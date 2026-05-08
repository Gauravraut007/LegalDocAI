from django.contrib import admin

from .models import Document


@admin.register(Document)
class DocumentAdmin(admin.ModelAdmin):
    list_display = ("title", "original_filename", "workspace", "uploaded_by",
                    "doc_type", "status", "size_bytes", "created_at")
    list_filter = ("status", "doc_type")
    search_fields = ("title", "original_filename", "sha256",
                     "workspace__name", "uploaded_by__email")
    readonly_fields = ("id", "sha256", "created_at", "updated_at")
