"""In-memory test doubles for owner-A/B/C surfaces.

These are *only* for the unit/integration tests — they do **not** ship in
the wheel and they make no attempt to model production semantics like
disk persistence, atomicity, or concurrent writers. They satisfy the
Protocols declared in ``litemiro.interfaces`` so any owner can wire
modules together without depending on another owner's real code.

The first half of this module (``InMemoryStateStore``, ``InMemoryEventLogger``)
is mirrored byte-for-byte from phase-2-B's ``tests/fakes.py``. Mirroring
(rather than importing) lets ``phase-2-A`` ship and merge independently of
``phase-2-B``; once both branches land, the duplicate block is collapsed
in a separate cleanup PR.

The second half adds A-surface fakes that phase-2-B does not own
(``FakeSocialGraph``, ``FakeFeedEngine``, ``FakeActionSelector``,
``FakeTopicExtractor``, ``FakeTokenBudgetManager``). These are used by
``core/`` unit tests until phase-2-B's real implementations land in main.
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping

from litemiro.models import Action, ActionContext, ActionType, Agent, Post, RoundEvent

# ---------------------------------------------------------------------------
# Mirrored from phase-2-B `tests/fakes.py` — keep byte-identical.
# ---------------------------------------------------------------------------


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

    Tests can inspect ``events`` (a tuple snapshot) to assert what
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


# ---------------------------------------------------------------------------
# A-surface additions — not in phase-2-B's `tests/fakes.py`.
# ---------------------------------------------------------------------------


class FakeSocialGraph:
    """``SocialGraphLike`` fake with the same semantics as phase-2-B's
    real ``SocialGraph``: idempotent follow/unfollow, self-follow rejected,
    deterministic ``to_dict`` (sorted, empty entries omitted).

    Discarded once phase-2-B's ``SocialGraph`` lands in main.
    """

    def __init__(self) -> None:
        self._following: dict[str, set[str]] = {}
        self._followers: dict[str, set[str]] = {}

    def follow(self, follower: str, followee: str) -> None:
        if follower == followee:
            raise ValueError(f"agent {follower!r} cannot self-follow")
        self._following.setdefault(follower, set()).add(followee)
        self._followers.setdefault(followee, set()).add(follower)

    def unfollow(self, follower: str, followee: str) -> None:
        followees = self._following.get(follower)
        if followees is not None:
            followees.discard(followee)
            if not followees:
                del self._following[follower]
        followers = self._followers.get(followee)
        if followers is not None:
            followers.discard(follower)
            if not followers:
                del self._followers[followee]

    def followers(self, agent_id: str) -> frozenset[str]:
        return frozenset(self._followers.get(agent_id, ()))

    def following(self, agent_id: str) -> frozenset[str]:
        return frozenset(self._following.get(agent_id, ()))

    def follower_count(self, agent_id: str) -> int:
        return len(self._followers.get(agent_id, ()))

    def following_count(self, agent_id: str) -> int:
        return len(self._following.get(agent_id, ()))

    def to_dict(self) -> dict[str, list[str]]:
        return {
            follower: sorted(followees)
            for follower, followees in sorted(self._following.items())
            if followees
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Iterable[str]]) -> FakeSocialGraph:
        graph = cls()
        for follower, followees in data.items():
            for followee in followees:
                graph.follow(follower, followee)
        return graph


class FakeFeedEngine:
    """``FeedEngineLike`` fake — records calls, returns canned feeds.

    ``build_feed`` returns whatever was queued via ``set_feed_for(agent_id)``;
    falls back to ``()`` if nothing is queued. ``index_post`` and
    ``update_engagement`` simply record their arguments so assertions can
    check call order.
    """

    def __init__(self) -> None:
        self._feeds: dict[str, tuple[Post, ...]] = {}
        self.indexed: list[Post] = []
        self.removed: list[str] = []
        self.engaged: list[Post] = []
        self.build_feed_calls: list[tuple[str, int, int]] = []

    def set_feed_for(self, agent_id: str, posts: tuple[Post, ...]) -> None:
        self._feeds[agent_id] = posts

    def index_post(self, post: Post) -> None:
        if any(p.post_id == post.post_id for p in self.indexed):
            raise ValueError(f"post already indexed: {post.post_id}")
        self.indexed.append(post)

    def remove_post(self, post_id: str) -> None:
        self.removed.append(post_id)

    def update_engagement(self, post: Post) -> None:
        if not any(p.post_id == post.post_id for p in self.indexed):
            raise KeyError(f"unknown post_id: {post.post_id}")
        # Latest engagement wins — replace any older snapshot.
        self.indexed = [p for p in self.indexed if p.post_id != post.post_id] + [post]
        self.engaged.append(post)

    def build_feed(
        self, *, agent: Agent, current_round: int, limit: int = 20
    ) -> tuple[Post, ...]:
        self.build_feed_calls.append((agent.agent_id, current_round, limit))
        feed = self._feeds.get(agent.agent_id, ())
        return feed[:limit]


class FakeActionSelector:
    """``ActionSelectorLike`` fake — replays queued ``Action`` per agent.

    ``queue_for(agent_id, *actions)`` schedules responses; if no response
    is queued, returns ``Action(type=DO_NOTHING)`` so the round still
    progresses without raising.
    """

    def __init__(self) -> None:
        self._queues: dict[str, list[Action]] = defaultdict(list)
        self.calls: list[tuple[str, ActionContext]] = []

    def queue_for(self, agent_id: str, *actions: Action) -> None:
        self._queues[agent_id].extend(actions)

    async def select_action(self, agent_id: str, context: ActionContext) -> Action:
        self.calls.append((agent_id, context))
        queue = self._queues.get(agent_id)
        if queue:
            return queue.pop(0)
        return Action(type=ActionType.DO_NOTHING)


class FakeTopicExtractor:
    """``TopicExtractorLike`` fake — content → pre-defined topics."""

    def __init__(self, mapping: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self._mapping: dict[str, tuple[str, ...]] = dict(mapping or {})
        self.calls: list[str] = []

    def set_topics(self, content: str, topics: tuple[str, ...]) -> None:
        self._mapping[content] = topics

    def extract(self, content: str) -> tuple[str, ...]:
        self.calls.append(content)
        return self._mapping.get(content, ())


class FakeTokenBudgetManager:
    """``TokenBudgetManagerLike`` fake — flat budget with consume tracking.

    Defaults to unlimited (``has_budget=True`` always). Use ``set_remaining``
    to simulate exhaustion in tests.
    """

    def __init__(self, *, initial_remaining: int = 1_000_000) -> None:
        self._remaining = initial_remaining
        self.has_budget_calls: list[int] = []
        self.consume_calls: list[int] = []

    def set_remaining(self, value: int) -> None:
        self._remaining = value

    def has_budget(self, *, estimated_tokens: int) -> bool:
        self.has_budget_calls.append(estimated_tokens)
        return self._remaining >= estimated_tokens

    def consume(self, *, tokens_used: int) -> None:
        self.consume_calls.append(tokens_used)
        self._remaining = max(0, self._remaining - tokens_used)

    def remaining(self) -> int:
        return self._remaining


__all__ = [
    "FakeActionSelector",
    "FakeFeedEngine",
    "FakeSocialGraph",
    "FakeTokenBudgetManager",
    "FakeTopicExtractor",
    "InMemoryEventLogger",
    "InMemoryStateStore",
]
