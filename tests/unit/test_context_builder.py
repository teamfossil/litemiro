"""Behaviour pinning for ``build_context``.

Covers field-by-field assembly, feed_limit forwarding, follower /
following count fidelity, recent_actions iterable acceptance, and
input validation.
"""

from __future__ import annotations

from collections.abc import Callable

import pytest

from litemiro.core import build_context
from litemiro.models import Action, ActionContext, ActionType, Agent, Post
from tests.fakes import FakeFeedEngine, FakeSocialGraph


def test_returns_action_context(make_agent: Callable[..., Agent]) -> None:
    ctx = build_context(
        agent=make_agent(),
        feed=FakeFeedEngine(),
        social=FakeSocialGraph(),
        recent_actions=(),
        round_num=0,
    )
    assert isinstance(ctx, ActionContext)


def test_feed_limit_forwarded(
    make_agent: Callable[..., Agent], make_post: Callable[..., Post]
) -> None:
    feed = FakeFeedEngine()
    agent = make_agent()
    posts = tuple(
        make_post(post_id=f"p-{n}", author_id="other") for n in range(30)
    )
    feed.set_feed_for(agent.agent_id, posts)

    ctx = build_context(
        agent=agent,
        feed=feed,
        social=FakeSocialGraph(),
        recent_actions=(),
        round_num=2,
        feed_limit=5,
    )
    assert len(ctx.feed) == 5
    assert feed.build_feed_calls == [(agent.agent_id, 2, 5)]


def test_follower_following_counts(make_agent: Callable[..., Agent]) -> None:
    social = FakeSocialGraph()
    social.follow("a-1", "target")   # target gets +1 follower
    social.follow("a-2", "target")
    social.follow("target", "a-3")   # target gets +1 following

    ctx = build_context(
        agent=make_agent(agent_id="target"),
        feed=FakeFeedEngine(),
        social=social,
        recent_actions=(),
        round_num=0,
    )
    assert ctx.follower_count == 2
    assert ctx.following_count == 1


def test_recent_actions_accepts_iterable(
    make_agent: Callable[..., Agent],
) -> None:
    actions = [
        Action(type=ActionType.LIKE_POST, target_post_id="p-1"),
        Action(type=ActionType.DO_NOTHING),
    ]
    ctx = build_context(
        agent=make_agent(),
        feed=FakeFeedEngine(),
        social=FakeSocialGraph(),
        recent_actions=iter(actions),   # generator-style input
        round_num=0,
    )
    assert ctx.recent_actions == tuple(actions)


def test_round_num_propagates(make_agent: Callable[..., Agent]) -> None:
    ctx = build_context(
        agent=make_agent(),
        feed=FakeFeedEngine(),
        social=FakeSocialGraph(),
        recent_actions=(),
        round_num=42,
    )
    assert ctx.round_num == 42


def test_feed_limit_zero_yields_empty_feed(
    make_agent: Callable[..., Agent], make_post: Callable[..., Post]
) -> None:
    feed = FakeFeedEngine()
    agent = make_agent()
    feed.set_feed_for(agent.agent_id, (make_post(post_id="p-1"),))
    ctx = build_context(
        agent=agent,
        feed=feed,
        social=FakeSocialGraph(),
        recent_actions=(),
        round_num=0,
        feed_limit=0,
    )
    assert ctx.feed == ()


def test_negative_round_rejected(make_agent: Callable[..., Agent]) -> None:
    with pytest.raises(ValueError, match="round_num"):
        build_context(
            agent=make_agent(),
            feed=FakeFeedEngine(),
            social=FakeSocialGraph(),
            recent_actions=(),
            round_num=-1,
        )


def test_negative_feed_limit_rejected(make_agent: Callable[..., Agent]) -> None:
    with pytest.raises(ValueError, match="feed_limit"):
        build_context(
            agent=make_agent(),
            feed=FakeFeedEngine(),
            social=FakeSocialGraph(),
            recent_actions=(),
            round_num=0,
            feed_limit=-1,
        )
