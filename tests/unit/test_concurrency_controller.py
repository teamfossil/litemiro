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
    async def test_50_items_form_three_batches(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        assert sleep_calls == [0.5, 0.5]

    async def test_exact_multiple_of_batch_size(self, monkeypatch: pytest.MonkeyPatch) -> None:
        sleep_calls: list[float] = []

        async def fake_sleep(delay: float) -> None:
            sleep_calls.append(delay)

        monkeypatch.setattr(asyncio, "sleep", fake_sleep)

        c = ConcurrencyController(semaphore_limit=5, batch_size=10, cooldown_seconds=1.0)
        items = tuple(f"x-{n}" for n in range(20))

        async def factory(item: str) -> str:
            return item

        await c.run_batched(items, factory)
        assert sleep_calls == [1.0]

    async def test_single_batch_has_no_cooldown(self, monkeypatch: pytest.MonkeyPatch) -> None:
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
        c = ConcurrencyController(semaphore_limit=3, batch_size=20, cooldown_seconds=0.0)
        in_flight = 0
        max_in_flight = 0
        gate = asyncio.Event()
        observed_lock = asyncio.Lock()

        async def factory(item: str) -> str:
            nonlocal in_flight, max_in_flight
            async with observed_lock:
                in_flight += 1
                max_in_flight = max(max_in_flight, in_flight)
            await asyncio.sleep(0)
            await gate.wait()
            async with observed_lock:
                in_flight -= 1
            return item

        async def release_gate() -> None:
            for _ in range(50):
                await asyncio.sleep(0)
            gate.set()

        items = tuple(f"i-{n}" for n in range(10))
        await asyncio.gather(c.run_batched(items, factory), release_gate())
        assert max_in_flight <= 3


class TestResultOrder:
    async def test_output_matches_input_order_despite_completion_order(self) -> None:
        c = ConcurrencyController(semaphore_limit=5, batch_size=10, cooldown_seconds=0.0)
        # First items take longest so completion order is reversed.
        delays = {f"i-{n}": (10 - n) * 0.001 for n in range(10)}

        async def factory(item: str) -> str:
            await asyncio.sleep(delays[item])
            return item.upper()

        items = tuple(f"i-{n}" for n in range(10))
        result = await c.run_batched(items, factory)
        assert result == tuple(it.upper() for it in items)


class TestFailurePropagation:
    async def test_uncaught_exception_propagates(self) -> None:
        c = ConcurrencyController(semaphore_limit=2, batch_size=4, cooldown_seconds=0.0)

        async def factory(item: str) -> str:
            if item == "boom":
                raise RuntimeError("explode")
            return item

        items = ("ok-1", "boom", "ok-2")
        with pytest.raises(RuntimeError, match="explode"):
            await c.run_batched(items, factory)


_AsyncFactory = Callable[[str], Awaitable[str]]
