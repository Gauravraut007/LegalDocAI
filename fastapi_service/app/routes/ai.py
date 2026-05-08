"""Back-compat alias router for chat (Phase 3).

The chat router lives in :mod:`app.routes.chat`. This module simply re-exports
its router under the name ``ai_router`` so :mod:`app.main` can include it.
"""
from app.routes.chat import router

__all__ = ["router"]
