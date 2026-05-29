from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from litemiro.models import Action, ActionContext, Agent

if TYPE_CHECKING:
    from litemiro.interfaces import FeedEngineLike, SocialGraphLike


def build_context(
    *,
    agent: Agent,
    feed: FeedEngineLike,
    social: SocialGraphLike,
    recent_actions: Iterable[Action],
    round_num: int,
    feed_limit: int = 20,
) -> ActionContext:
    if round_num < 0:
        raise ValueError(f"round_num must be >= 0, got {round_num}")
    if feed_limit < 0:
        raise ValueError(f"feed_limit must be >= 0, got {feed_limit}")
    return ActionContext(
        agent=agent,
        feed=feed.build_feed(agent=agent, current_round=round_num, limit=feed_limit),
        recent_actions=tuple(recent_actions),
        follower_count=social.follower_count(agent.agent_id),
        following_count=social.following_count(agent.agent_id),
        following_ids=social.following(agent.agent_id),
        round_num=round_num,
    )


__all__ = ["build_context"]
