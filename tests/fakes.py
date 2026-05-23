"""In-memory test doubles for owner-boundary Protocols."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Iterable, Mapping

from litemiro.models import (
    Action,
    ActionContext,
    ActionResult,
    ActionType,
    Agent,
    LLMMeta,
    Post,
    RoundEvent,
)


class InMemoryStateStore:
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
        if agent.agent_id in self._agents:
            raise KeyError(f"agent already exists: {agent.agent_id}")
        self._agents[agent.agent_id] = agent


class InMemoryEventLogger:
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


class FakeSocialGraph:
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
        self.indexed = [p for p in self.indexed if p.post_id != post.post_id] + [post]
        self.engaged.append(post)

    def build_feed(self, *, agent: Agent, current_round: int, limit: int = 20) -> tuple[Post, ...]:
        self.build_feed_calls.append((agent.agent_id, current_round, limit))
        feed = self._feeds.get(agent.agent_id, ())
        return feed[:limit]


class FakeActionSelector:
    def __init__(self, *, model: str = "fake-model") -> None:
        self._queues: dict[str, list[Action]] = defaultdict(list)
        self._model = model
        self.calls: list[tuple[str, ActionContext]] = []

    def queue_for(self, agent_id: str, *actions: Action) -> None:
        self._queues[agent_id].extend(actions)

    async def select_action(self, agent_id: str, context: ActionContext) -> ActionResult:
        self.calls.append((agent_id, context))
        queue = self._queues.get(agent_id)
        action = queue.pop(0) if queue else Action(type=ActionType.DO_NOTHING)
        return ActionResult(
            action=action,
            llm_meta=LLMMeta(model=self._model, tokens_used=0, latency_ms=0.0),
        )


class FakeTopicExtractor:
    def __init__(self, mapping: Mapping[str, tuple[str, ...]] | None = None) -> None:
        self._mapping: dict[str, tuple[str, ...]] = dict(mapping or {})
        self.calls: list[str] = []

    def set_topics(self, content: str, topics: tuple[str, ...]) -> None:
        self._mapping[content] = topics

    def extract(self, content: str) -> tuple[str, ...]:
        self.calls.append(content)
        return self._mapping.get(content, ())


class FakeTokenBudgetManager:
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
