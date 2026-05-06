"""Shared fixtures for litemiro test suites.

Kept light on purpose — anything specific to one component lives next to
that component's tests. The fakes here only model the *owner-boundary
Protocols* declared in ``litemiro.interfaces``.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from litemiro.interfaces import LLMClient
from litemiro.models import Agent, Post


@pytest.fixture
def utc_now() -> datetime:
    """Aware UTC timestamp suitable for ``RoundEvent.timestamp``."""
    return datetime(2026, 4, 1, 10, 0, tzinfo=UTC)


@pytest.fixture
def make_agent() -> Callable[..., Agent]:
    """Factory for ``Agent`` with sensible defaults."""

    def _make(agent_id: str = "a-001", **overrides: Any) -> Agent:
        defaults: dict[str, Any] = {
            "agent_id": agent_id,
            "interests": ("ai", "music"),
            "persona_traits": {"tone": "curious"},
            "memory_summary": None,
            "activation_rate": 0.5,
        }
        defaults.update(overrides)
        return Agent.model_validate(defaults)

    return _make


@pytest.fixture
def make_post() -> Callable[..., Post]:
    """Factory for ``Post`` with sensible defaults."""

    def _make(post_id: str = "p-1", **overrides: Any) -> Post:
        defaults: dict[str, Any] = {
            "post_id": post_id,
            "author_id": "a-001",
            "content": "hello world",
            "topics": ("ai",),
            "created_round": 0,
        }
        defaults.update(overrides)
        return Post.model_validate(defaults)

    return _make


class _FakeLLMClient:
    """Deterministic in-memory LLM for tests.

    Records every call and replays a queue of pre-set responses.
    """

    def __init__(self, *responses: str) -> None:
        self._responses: list[str] = list(responses)
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, *, system: str, user: str, model: str) -> str:
        self.calls.append((system, user, model))
        if not self._responses:
            raise RuntimeError("FakeLLMClient: no more queued responses")
        return self._responses.pop(0)


@pytest.fixture
def fake_llm() -> Callable[..., LLMClient]:
    """Build a fresh :class:`_FakeLLMClient` per test."""

    def _make(*responses: str) -> LLMClient:
        return _FakeLLMClient(*responses)

    return _make
