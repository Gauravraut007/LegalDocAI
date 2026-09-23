"""Template helpers for the web UI."""
from __future__ import annotations

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()

_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_ITALIC_RE = re.compile(r"(?<!\*)\*([^*\n]+?)\*(?!\*)")
_INLINE_CODE_RE = re.compile(r"`([^`\n]+?)`")
_CITATION_RE = re.compile(r"\[S(\d+)\]")


@register.filter
def chat_format(value):
    """Escape chat content, then render a small safe markdown subset."""
    text = escape(value or "")
    text = _BOLD_RE.sub(r"<strong>\1</strong>", text)
    text = _ITALIC_RE.sub(r"<em>\1</em>", text)
    text = _INLINE_CODE_RE.sub(r"<code>\1</code>", text)
    text = _CITATION_RE.sub(r'<sup class="cite">S\1</sup>', text)
    text = text.replace("\n", "<br>")
    return mark_safe(text)


_STATUS_COLOR = {
    "ready": "success",
    "uploaded": "secondary",
    "queued": "info",
    "processing": "warning",
    "failed": "danger",
}
_STATUS_ICON = {
    "ready": "check-circle-fill",
    "uploaded": "cloud-arrow-up",
    "queued": "hourglass-split",
    "processing": "arrow-repeat",
    "failed": "exclamation-octagon-fill",
}


@register.filter
def status_color(value):
    return _STATUS_COLOR.get(str(value), "secondary")


@register.filter
def status_icon(value):
    return _STATUS_ICON.get(str(value), "circle")


@register.filter
def filesize(num):
    try:
        n = float(num)
    except (TypeError, ValueError):
        return "—"
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@register.filter
def get_item(d, key):
    if isinstance(d, dict):
        return d.get(key)
    return None
