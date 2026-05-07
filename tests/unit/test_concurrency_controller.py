"""Behaviour pinning for ``ConcurrencyController``.

The four contract surfaces:

* construction validation (semaphore_limit / batch_size / cooldown bounds)
* batching — items split into fixed-size chunks; trailing chunk smaller
* semaphore — concurrent in-flight coroutines never exceed the limit
* cooldown — `asyncio.sleep` called exactly `batches - 1` times
* result order — output matches input order regardless of completion order
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from litemiro.core import ConcurrencyController


class TestConstruction:
    @pytest.mark.parametrize(
        ("kwargs", "match"),
        [
            ({"semaphore_limit": 0}, "semaphore_limit"),
            ({"batch_size": 0}, "batch_size"),
            ({"cooldown_seconds": -0.1}, "cooldown_seconds"),
        ],
    )
    def test_invalid_args_rejected(self, kwargs: dict[str, Any], match: str) -> None:
        with pytest.raises(ValueError, match=match):
            ConcurrencyController(**kwargs)

    def test_defaults_match_design_doc(self) -> None:
        c = ConcurrencyController()
        assert c._semaphore_limit == 10
        assert c._batch_size == 20
        assert c._cooldown_seconds == 0.5


class TestEmptyAndSingle:
    async def test_empty_items_yield_empty_tuple(self) -> None:
        c = ConcurrencyController()

        async def factory(_: str) -> int:
            return 0

        assert await c.run_batched((), factory) == ()

    async def test_single_item_runs_factory_once(self) -> None:
        c = ConcurrencyController()
        calls: list[str] = []

        async def factory(item: str) -> str:
            calls.append(item)
            return item.upper()

        result = await c.run_batched(("hi",), factory)
        assert result == ("HI",)
        assert calls == ["hi"]


class TestBatchSplit:
    async def test_50_items_form_three_batches(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # batch_size=20, items=50 → batches [20, 20, 10] → 2 cooldowns.
        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        c = ConcurrencyController(semaphore_limit=10, batch_size=20, cooldown_seconds=0.5)
        items = tuple(f"i-{n:02d}" for n in range(50))

        async def factory(item: str) -> str:
            return item

        results = await c.run_batched(items, factory)
        assert results == items
        assert sleep_calls == [0.5, 0.5]   # exactly batches - 1

    async def test_exact_multiple_of_batch_size(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        c = ConcurrencyController(semaphore_limit=5, batch_size=10, cooldown_seconds=1.0)
        items = tuple(f"x-{n}" for n in range(20))   # exactly 2 batches

        async def factory(item: str) -> str:
            return item

        await c.run_batched(items, factory)
        assert sleep_calls == [1.0]   # 1 cooldown between 2 batches

    async def test_single_batch_has_no_cooldown(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        c = ConcurrencyController(batch_size=20, cooldown_seconds=0.5)

        async def factory(item: str) -> str:
            return item

        await c.run_batched(tuple(f"i-{n}" for n in range(15)), factory)
        assert sleep_calls == []


class TestSemaphoreLimit:
    async def test_max_in_flight_does_not_exceed_limit(self) -> None:
        c = ConcurrencyController(
            semaphore_limit=3, batch_size=20, cooldown_seconds=0.0
        )
        in_flight = 0
        max_in_flight = 0
        # Stage all factories at the same await so concurrent entry can be observed.
        gate = asyncio.Event()
        observed_lock = asyncio.Lock()

        async def factory(item: str) -> str:
            nonlocal in_flight, max_in_flight
            async with observed_lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            # Yield control so other coroutines can enter the semaphore.
            await asyncio.sleep(0)
            await gate.wait()
            async with observed_lock:
                in_flight -= 1
            return item

        async def release_gate() -> None:
            # Let a few event-loop ticks pass so all 10 items try to enter.
            for _ in range(50):
                await asyncio.sleep(0)
            gate.set()

        items = tuple(f"i-{n}" for n in range(10))
        await asyncio.gather(c.run_batched(items, factory), release_gate())
        assert max_in_flight <= 3


class TestResultOrder:
    async def test_output_matches_input_order_despite_completion_order(self) -> None:
        c = ConcurrencyController(
            semaphore_limit=5, batch_size=10, cooldown_seconds=0.0
        )
        # First items take longest — without ordering, results would be reversed.
        delays = {f"i-{n}": (10 - n) * 0.001 for n in range(10)}

        async def factory(item: str) -> str:
            await asyncio.sleep(delays[item])
            return item.upper()

        items = tuple(f"i-{n}" for n in range(10))
        result = await c.run_batched(items, factory)
        assert result == tuple(it.upper() for it in items)


class TestFailurePropagation:
    async def test_uncaught_exception_propagates(self) -> None:
        c = ConcurrencyController(
            semaphore_limit=2, batch_size=4, cooldown_seconds=0.0
        )

        async def factory(item: str) -> str:
            if item == "boom":
                raise RuntimeError("explode")
            return item

        items = ("ok-1", "boom", "ok-2")
        with pytest.raises(RuntimeError, match="explode"):
            await c.run_batched(items, factory)


# pytest's auto async mode handles unmarked coroutines; these aliases keep
# annotations honest for mypy / readers.
_AsyncFactory = Callable[[str], Awaitable[str]]
