"""Template helpers for the web UI."""
from __future__ import annotations

from django import template

register = template.Library()


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
