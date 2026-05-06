"""In-memory test doubles for owner-A/C surfaces.

These are *only* for B's unit/integration tests — they do **not** ship in
the wheel and they make no attempt to model production semantics like
disk persistence, atomicity, or concurrent writers. They satisfy the
``StateStoreLike`` and ``EventLoggerLike`` Protocols so B can wire
modules together without depending on A's or C's real code.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping

from litemiro.models import Agent, Post, RoundEvent


class InMemoryStateStore:
    """Trivial dict-backed ``StateStoreLike``.

    Random seeds are derived deterministically from ``agent_id`` so two
    test runs with the same seed produce the same agent decisions.
    """

    def __init__(
        self,
        *,
        agents: Mapping[str, Agent] | None = None,
        posts: Mapping[str, Post] | None = None,
        global_seed: int = 0,
    ) -> None:
        self._agents: dict[str, Agent] = dict(agents or {})
        self._posts: dict[str, Post] = dict(posts or {})
        self._global_seed = global_seed

    def get_agent(self, agent_id: str) -> Agent:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"unknown agent_id: {agent_id}") from exc

    def list_agent_ids(self) -> tuple[str, ...]:
        return tuple(self._agents)

    def get_post(self, post_id: str) -> Post:
        try:
            return self._posts[post_id]
        except KeyError as exc:
            raise KeyError(f"unknown post_id: {post_id}") from exc

    def list_posts(self) -> tuple[Post, ...]:
        return tuple(self._posts.values())

    def add_post(self, post: Post) -> None:
        if post.post_id in self._posts:
            raise KeyError(f"post already exists: {post.post_id}")
        self._posts[post.post_id] = post

    def replace_post(self, post: Post) -> None:
        if post.post_id not in self._posts:
            raise KeyError(f"unknown post_id: {post.post_id}")
        self._posts[post.post_id] = post

    def get_random_seed(self, agent_id: str) -> int:
        digest = hashlib.sha256(f"{self._global_seed}:{agent_id}".encode()).digest()
        return int.from_bytes(digest[:8], "big", signed=False)

    def add_agent(self, agent: Agent) -> None:
        """Test-only convenience — *not* on the Protocol."""
        if agent.agent_id in self._agents:
            raise KeyError(f"agent already exists: {agent.agent_id}")
        self._agents[agent.agent_id] = agent


class InMemoryEventLogger:
    """List-backed ``EventLoggerLike`` that records every event.

    Tests can inspect ``events`` (a tuple snapshot) to assert what B's
    components emitted. Calls after :meth:`aclose` raise ``RuntimeError``.
    """

    def __init__(self) -> None:
        self._events: list[RoundEvent] = []
        self._closed = False

    async def log_event(self, event: RoundEvent) -> None:
        if self._closed:
            raise RuntimeError("logger is closed")
        self._events.append(event)

    async def aclose(self) -> None:
        self._closed = True

    @property
    def events(self) -> tuple[RoundEvent, ...]:
        return tuple(self._events)

    @property
    def is_closed(self) -> bool:
        return self._closed
