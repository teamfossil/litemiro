from __future__ import annotations

import pytest

from litemiro.interfaces import (
    ActionSelectorLike,
    EventLoggerLike,
    FeedEngineLike,
    SocialGraphLike,
    StateStoreLike,
    TokenBudgetManagerLike,
    TopicExtractorLike,
)
from litemiro.models import Action, ActionContext, ActionType, Post, RoundEvent
from tests.fakes import (
    FakeActionSelector,
    FakeFeedEngine,
    FakeSocialGraph,
    FakeTokenBudgetManager,
    FakeTopicExtractor,
    InMemoryEventLogger,
    InMemoryStateStore,
)


def test_in_memory_state_store_satisfies_protocol() -> None:
    assert isinstance(InMemoryStateStore(), StateStoreLike)


def test_in_memory_event_logger_satisfies_protocol() -> None:
    assert isinstance(InMemoryEventLogger(), EventLoggerLike)


def test_fake_social_graph_satisfies_protocol() -> None:
    assert isinstance(FakeSocialGraph(), SocialGraphLike)


def test_fake_feed_engine_satisfies_protocol() -> None:
    assert isinstance(FakeFeedEngine(), FeedEngineLike)


def test_fake_action_selector_satisfies_protocol() -> None:
    assert isinstance(FakeActionSelector(), ActionSelectorLike)


def test_fake_topic_extractor_satisfies_protocol() -> None:
    assert isinstance(FakeTopicExtractor(), TopicExtractorLike)


def test_fake_token_budget_satisfies_protocol() -> None:
    assert isinstance(FakeTokenBudgetManager(), TokenBudgetManagerLike)


def test_in_memory_state_store_seed_is_deterministic() -> None:
    s = InMemoryStateStore(global_seed=42)
    assert s.get_random_seed("agent-A") == s.get_random_seed("agent-A")
    assert s.get_random_seed("agent-A") != s.get_random_seed("agent-B")


def test_in_memory_state_store_post_round_trip() -> None:
    s = InMemoryStateStore()
    post = Post(post_id="p-1", author_id="a-1", content="hi", created_round=0)
    s.add_post(post)
    assert s.get_post("p-1") == post
    with pytest.raises(KeyError):
        s.add_post(post)


async def test_in_memory_event_logger_records_and_closes(utc_now) -> None:
    logger = InMemoryEventLogger()
    event = RoundEvent(
        round_num=0,
        timestamp=utc_now,
        agent_id="a-1",
        action=Action(type=ActionType.DO_NOTHING),
    )
    await logger.log_event(event)
    assert logger.events == (event,)
    await logger.aclose()
    with pytest.raises(RuntimeError):
        await logger.log_event(event)


def test_fake_social_graph_self_follow_rejected() -> None:
    g = FakeSocialGraph()
    with pytest.raises(ValueError):
        g.follow("a", "a")


def test_fake_social_graph_to_dict_is_sorted() -> None:
    g = FakeSocialGraph()
    g.follow("a", "c")
    g.follow("a", "b")
    g.follow("b", "a")
    assert g.to_dict() == {"a": ["b", "c"], "b": ["a"]}


def test_fake_feed_engine_records_calls(make_agent) -> None:
    f = FakeFeedEngine()
    agent = make_agent()
    result = f.build_feed(agent=agent, current_round=3, limit=5)
    assert result == ()
    assert f.build_feed_calls == [(agent.agent_id, 3, 5)]


async def test_fake_action_selector_replays_queue(make_agent) -> None:
    selector = FakeActionSelector()
    agent = make_agent()
    ctx = ActionContext(agent=agent, round_num=0)
    queued = Action(type=ActionType.CREATE_POST, content="hi")
    selector.queue_for(agent.agent_id, queued)
    assert await selector.select_action(agent.agent_id, ctx) == queued
    fallback = await selector.select_action(agent.agent_id, ctx)
    assert fallback.type is ActionType.DO_NOTHING


def test_fake_topic_extractor_returns_canned() -> None:
    e = FakeTopicExtractor({"hello world": ("ai", "music")})
    assert e.extract("hello world") == ("ai", "music")
    assert e.extract("unknown") == ()
    assert e.calls == ["hello world", "unknown"]


def test_fake_token_budget_consume_decrements_remaining() -> None:
    b = FakeTokenBudgetManager(initial_remaining=1000)
    assert b.has_budget(estimated_tokens=500)
    b.consume(tokens_used=400)
    assert b.remaining() == 600
    b.consume(tokens_used=1000)
    assert b.remaining() == 0


def test_state_store_fixture_is_fresh(state_store: InMemoryStateStore) -> None:
    assert state_store.list_agent_ids() == ()


def test_event_logger_fixture_is_fresh(event_logger: InMemoryEventLogger) -> None:
    assert event_logger.events == ()
    assert not event_logger.is_closed
