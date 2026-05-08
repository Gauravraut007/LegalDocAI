"""Shared async helpers."""
from __future__ import annotations

from asgiref.sync import sync_to_async


async def get_object_or_404_async(model_cls, **filters):
    from django.http import Http404

    @sync_to_async
    def _get():
        try:
            return model_cls.objects.get(**filters)
        except model_cls.DoesNotExist:
            return None

    obj = await _get()
    if obj is None:
        raise Http404(f"{model_cls.__name__} not found")
    return obj
