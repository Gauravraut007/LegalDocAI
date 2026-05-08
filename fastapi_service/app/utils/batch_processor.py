"""Adaptive batch processing helpers."""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Iterable, List, TypeVar

T = TypeVar("T")
R = TypeVar("R")


async def process_in_batches(
    items: List[T],
    processor_fn: Callable[[List[T]], Awaitable[List[R]]],
    batch_size: int = 32,
    delay_between_batches: float = 0.0,
) -> List[R]:
    """Process `items` in fixed-size batches, preserving order."""
    if batch_size <= 0:
        raise ValueError("batch_size must be > 0")
    out: List[R] = []
    for i in range(0, len(items), batch_size):
        batch = items[i : i + batch_size]
        out.extend(await processor_fn(batch))
        if delay_between_batches > 0 and i + batch_size < len(items):
            await asyncio.sleep(delay_between_batches)
    return out


def iter_batches(items: Iterable[T], batch_size: int) -> Iterable[List[T]]:
    """Yield successive batches of `batch_size` items."""
    batch: List[T] = []
    for it in items:
        batch.append(it)
        if len(batch) >= batch_size:
            yield batch
            batch = []
    if batch:
        yield batch
