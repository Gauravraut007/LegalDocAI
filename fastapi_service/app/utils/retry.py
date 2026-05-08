"""Tenacity retry decorators for transient external/IO failures."""
from __future__ import annotations

import logging

from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = logging.getLogger(__name__)


def _gemini_exception_types() -> tuple[type[BaseException], ...]:
    types: list[type[BaseException]] = []
    try:  # pragma: no cover - depends on env
        from google.api_core import exceptions as g_exc

        types.extend(
            [
                g_exc.ResourceExhausted,
                g_exc.ServiceUnavailable,
                g_exc.DeadlineExceeded,
                g_exc.InternalServerError,
                g_exc.TooManyRequests,
            ]
        )
    except Exception:  # noqa: BLE001
        pass
    types.append(TimeoutError)
    types.append(ConnectionError)
    return tuple(types)


GEMINI_RETRY_EXCEPTIONS = _gemini_exception_types()


def gemini_retry(max_attempts: int = 3, min_wait: float = 4.0, max_wait: float = 60.0):
    """Decorator: exponential backoff for Gemini LLM calls."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(GEMINI_RETRY_EXCEPTIONS),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


def io_retry(max_attempts: int = 3, min_wait: float = 1.0, max_wait: float = 10.0):
    """Decorator: retry transient IO/network/model-load errors."""
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type((IOError, OSError, TimeoutError, ConnectionError)),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )


def db_retry(max_attempts: int = 3, min_wait: float = 0.5, max_wait: float = 5.0):
    """Decorator: retry transient DB errors (connection drops)."""
    try:  # pragma: no cover - depends on env
        from sqlalchemy.exc import DBAPIError, OperationalError

        retry_types: tuple[type[BaseException], ...] = (
            OperationalError,
            DBAPIError,
            ConnectionError,
            TimeoutError,
        )
    except Exception:  # noqa: BLE001
        retry_types = (ConnectionError, TimeoutError)
    return retry(
        stop=stop_after_attempt(max_attempts),
        wait=wait_exponential(multiplier=1, min=min_wait, max=max_wait),
        retry=retry_if_exception_type(retry_types),
        before_sleep=before_sleep_log(logger, logging.WARNING),
        reraise=True,
    )
