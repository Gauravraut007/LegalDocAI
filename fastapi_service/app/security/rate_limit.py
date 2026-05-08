"""Per-user Redis token-bucket rate limiter for chat endpoints.

Algorithm: classic token bucket persisted in Redis under
``rl:{scope}:{user_id}``. Two atomic Redis ops per request (GET + SET via
pipeline). Refills at ``rate / 60`` tokens per second up to ``burst``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Optional

from fastapi import Depends, HTTPException, status

from app.config import settings
from app.security.jwt import AuthenticatedUser, get_current_user

_REDIS_CLIENT = None  # type: ignore[assignment]


def _get_redis():
    global _REDIS_CLIENT
    if _REDIS_CLIENT is None:
        from redis import asyncio as aioredis

        _REDIS_CLIENT = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
    return _REDIS_CLIENT


@dataclass
class RateLimitDecision:
    allowed: bool
    remaining: float
    retry_after: float


async def consume(
    scope: str,
    user_id: str,
    *,
    rate_per_minute: int,
    burst: int,
    cost: float = 1.0,
) -> RateLimitDecision:
    refill_per_sec = rate_per_minute / 60.0
    capacity = max(burst, 1)
    key = f"rl:{scope}:{user_id}"
    now = time.time()

    redis = _get_redis()
    pipe = redis.pipeline()
    pipe.hgetall(key)
    res = await pipe.execute()
    state = res[0] or {}

    tokens = float(state.get("tokens", capacity))
    last = float(state.get("ts", now))
    tokens = min(capacity, tokens + (now - last) * refill_per_sec)

    if tokens >= cost:
        tokens -= cost
        allowed = True
        retry_after = 0.0
    else:
        allowed = False
        retry_after = (cost - tokens) / refill_per_sec

    await redis.hset(key, mapping={"tokens": tokens, "ts": now})
    await redis.expire(key, max(60, int(capacity / refill_per_sec) + 5))
    return RateLimitDecision(allowed=allowed, remaining=tokens, retry_after=retry_after)


def chat_rate_limit():
    """Dependency factory enforcing the chat rate limit."""

    async def _dep(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
        try:
            decision = await consume(
                "chat",
                str(user.user_id),
                rate_per_minute=settings.CHAT_RATE_LIMIT_PER_MINUTE,
                burst=settings.CHAT_RATE_LIMIT_BURST,
            )
        except Exception:  # noqa: BLE001 - never block on Redis failures
            return user
        if not decision.allowed:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="rate limit exceeded",
                headers={"Retry-After": f"{int(decision.retry_after) + 1}"},
            )
        return user

    return _dep
