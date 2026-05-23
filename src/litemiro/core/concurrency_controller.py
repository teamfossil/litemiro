from __future__ import annotations

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

_T = TypeVar("_T")


class ConcurrencyController:
    def __init__(
        self,
        *,
        semaphore_limit: int = 10,
        batch_size: int = 20,
        cooldown_seconds: float = 0.5,
    ) -> None:
        if semaphore_limit < 1:
            raise ValueError(f"semaphore_limit must be >= 1, got {semaphore_limit}")
        if batch_size < 1:
            raise ValueError(f"batch_size must be >= 1, got {batch_size}")
        if cooldown_seconds < 0:
            raise ValueError(f"cooldown_seconds must be >= 0, got {cooldown_seconds}")
        self._semaphore_limit = semaphore_limit
        self._batch_size = batch_size
        self._cooldown_seconds = cooldown_seconds

    async def run_batched(
        self,
        items: tuple[str, ...],
        task_factory: Callable[[str], Coroutine[Any, Any, _T]],
    ) -> tuple[_T, ...]:
        if not items:
            return ()

        semaphore = asyncio.Semaphore(self._semaphore_limit)

        async def _gated(item: str) -> _T:
            async with semaphore:
                return await task_factory(item)

        results: list[_T] = []
        batch_count = (len(items) + self._batch_size - 1) // self._batch_size
        for idx in range(batch_count):
            start = idx * self._batch_size
            stop = start + self._batch_size
            batch = items[start:stop]
            batch_results = await asyncio.gather(*(_gated(it) for it in batch))
            results.extend(batch_results)
            if idx < batch_count - 1:
                await asyncio.sleep(self._cooldown_seconds)
        return tuple(results)


__all__ = ["ConcurrencyController"]
