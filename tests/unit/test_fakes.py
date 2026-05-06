"""Behaviour tests for the in-memory test doubles.

The fakes are used as fixtures in many other tests, so a regression in
``tests/fakes.py`` would silently corrupt B's whole suite. These tests
pin the parts other tests rely on.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from litemiro.interfaces import EventLoggerLike, StateStoreLike
from litemiro.models import Action, ActionType, Agent, Post, RoundEvent
from tests.fakes import InMemoryEventLogger, InMemoryStateStore


def test_state_store_satisfies_protocol() -> None:
    assert isinstance(InMemoryStateStore(), StateStoreLike)


def test_event_logger_satisfies_protocol() -> None:
    assert isinstance(InMemoryEventLogger(), EventLoggerLike)


class TestInMemoryStateStore:
    def test_add_and_get_agent(self, make_agent: Callable[..., Agent]) -> None:
        store = InMemoryStateStore()
        a = make_agent("a-1")
        store.add_agent(a)
        assert store.get_agent("a-1") == a
        assert store.list_agent_ids() == ("a-1",)

    def test_get_unknown_agent_raises(self) -> None:
        with pytest.raises(KeyError, match="unknown agent_id"):
            InMemoryStateStore().get_agent("nope")

    def test_add_post_then_replace(self, make_post: Callable[..., Post]) -> None:
        store = InMemoryStateStore()
        p = make_post("p-1")
        store.add_post(p)
        assert store.list_posts() == (p,)
        updated = make_post("p-1", likes=5)
        store.replace_post(updated)
        assert store.get_post("p-1").likes == 5

    def test_double_add_post_rejected(self, make_post: Callable[..., Post]) -> None:
        store = InMemoryStateStore()
        store.add_post(make_post("p-1"))
        with pytest.raises(KeyError, match="post already exists"):
            store.add_post(make_post("p-1"))

    def test_replace_unknown_post_rejected(self, make_post: Callable[..., Post]) -> None:
        with pytest.raises(KeyError, match="unknown post_id"):
            InMemoryStateStore().replace_post(make_post("ghost"))

    def test_random_seed_deterministic_per_agent(self) -> None:
        store_a = InMemoryStateStore(global_seed=42)
        store_b = InMemoryStateStore(global_seed=42)
        assert store_a.get_random_seed("a-1") == store_b.get_random_seed("a-1")
        assert store_a.get_random_seed("a-1") != store_a.get_random_seed("a-2")

    def test_random_seed_changes_with_global_seed(self) -> None:
        s1 = InMemoryStateStore(global_seed=1).get_random_seed("a-1")
        s2 = InMemoryStateStore(global_seed=2).get_random_seed("a-1")
        assert s1 != s2

    def test_seed_constructor(
        self, make_agent: Callable[..., Agent], make_post: Callable[..., Post]
    ) -> None:
        a = make_agent("a-1")
        p = make_post("p-1")
        store = InMemoryStateStore(agents={"a-1": a}, posts={"p-1": p})
        assert store.list_agent_ids() == ("a-1",)
        assert store.list_posts() == (p,)


class TestInMemoryEventLogger:
    @staticmethod
    def _event() -> RoundEvent:
        return RoundEvent(
            round_num=0,
            timestamp=datetime(2026, 4, 1, 10, 0, tzinfo=UTC),
            agent_id="a-1",
            action=Action(type=ActionType.DO_NOTHING),
        )

    async def test_log_records_events(self) -> None:
        logger = InMemoryEventLogger()
        e = self._event()
        await logger.log_event(e)
        assert logger.events == (e,)

    async def test_aclose_blocks_further_logs(self) -> None:
        logger = InMemoryEventLogger()
        await logger.aclose()
        assert logger.is_closed is True
        with pytest.raises(RuntimeError, match="closed"):
            await logger.log_event(self._event())

    async def test_events_property_returns_snapshot(self) -> None:
        logger = InMemoryEventLogger()
        await logger.log_event(self._event())
        snap = logger.events
        await logger.log_event(self._event())
        # snapshot should not reflect later writes
        assert len(snap) == 1
        assert len(logger.events) == 2
