"""Smoke checks that owner-boundary Protocols are runtime-checkable.

These tests don't exercise behaviour — they just guarantee that any
in-memory fake satisfying the documented signature also satisfies
``isinstance(obj, ProtocolName)``. That way B's tests can use
``isinstance`` for clarity instead of structural-typing tricks.
"""

from __future__ import annotations

from collections.abc import Mapping

from litemiro.interfaces import (
    ActionSelectorLike,
    EventLoggerLike,
    FeedEngineLike,
    LLMClient,
    SocialGraphLike,
    StateStoreLike,
    TokenBudgetManagerLike,
    TopicExtractorLike,
)
from litemiro.models import (
    Action,
    ActionContext,
    ActionResult,
    ActionType,
    Agent,
    LLMMeta,
    LLMResponse,
    Post,
    RoundEvent,
)


class _StubGraph:
    def follow(self, follower: str, followee: str) -> None: ...
    def unfollow(self, follower: str, followee: str) -> None: ...
    def followers(self, agent_id: str) -> frozenset[str]:
        return frozenset()

    def following(self, agent_id: str) -> frozenset[str]:
        return frozenset()

    def follower_count(self, agent_id: str) -> int:
        return 0

    def following_count(self, agent_id: str) -> int:
        return 0

    def to_dict(self) -> Mapping[str, list[str]]:
        return {}


class _StubFeed:
    def index_post(self, post: Post) -> None: ...
    def remove_post(self, post_id: str) -> None: ...
    def update_engagement(self, post: Post) -> None: ...

    def build_feed(self, *, agent: Agent, current_round: int, limit: int = 20) -> tuple[Post, ...]:
        return ()


class _StubSelector:
    async def select_action(self, agent_id: str, context: ActionContext) -> ActionResult:
        return ActionResult(
            action=Action(type=ActionType.DO_NOTHING),
            llm_meta=LLMMeta(model="", tokens_used=0, latency_ms=0.0),
        )


class _StubStore:
    def get_agent(self, agent_id: str) -> Agent:
        return Agent(agent_id=agent_id)

    def list_agent_ids(self) -> tuple[str, ...]:
        return ()

    def get_post(self, post_id: str) -> Post:
        raise KeyError(post_id)

    def list_posts(self) -> tuple[Post, ...]:
        return ()

    def add_post(self, post: Post) -> None: ...
    def replace_post(self, post: Post) -> None: ...

    def get_random_seed(self, agent_id: str) -> int:
        return 0


class _StubLogger:
    async def log_event(self, event: RoundEvent) -> None: ...
    async def aclose(self) -> None: ...


class _StubLLM:
    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        return LLMResponse(content="")


class _StubTopicExtractor:
    def extract(self, content: str) -> tuple[str, ...]:
        return ()


class _StubTokenBudget:
    def has_budget(self, *, estimated_tokens: int) -> bool:
        return True

    def consume(self, *, tokens_used: int) -> None: ...

    def remaining(self) -> int:
        return 0


def test_social_graph_protocol() -> None:
    assert isinstance(_StubGraph(), SocialGraphLike)


def test_feed_engine_protocol() -> None:
    assert isinstance(_StubFeed(), FeedEngineLike)


def test_action_selector_protocol() -> None:
    assert isinstance(_StubSelector(), ActionSelectorLike)


def test_state_store_protocol() -> None:
    assert isinstance(_StubStore(), StateStoreLike)


def test_event_logger_protocol() -> None:
    assert isinstance(_StubLogger(), EventLoggerLike)


def test_llm_client_protocol() -> None:
    assert isinstance(_StubLLM(), LLMClient)


def test_topic_extractor_protocol() -> None:
    assert isinstance(_StubTopicExtractor(), TopicExtractorLike)


def test_token_budget_protocol() -> None:
    assert isinstance(_StubTokenBudget(), TokenBudgetManagerLike)
