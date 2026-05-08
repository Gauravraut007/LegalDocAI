"""Celery app + workers package."""
from app.workers.tasks import celery_app

__all__ = ["celery_app"]
