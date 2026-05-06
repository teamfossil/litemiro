"""TDD spec for ``litemiro.feed.engine.FeedEngine``.

Notion §3.2 says only "FeedEngine → SocialGraph: 팔로우 관계 기반 피드
필터링" and §9.2 only lists generic test-points. B locks the contract:

* **Candidate pool** = (posts whose author the agent *follows*)
  unioned with (posts whose ``topics`` overlap with the agent's
  ``interests``).
* **Self-authored posts are excluded** so DO_NOTHING/CREATE_POST loops
  don't echo into the agent's own feed.
* **Ranking** = ``hot_score`` desc, ``post_id`` ascending tie-break →
  the order is deterministic across processes and across runs.
* ``index_post`` rejects duplicates; ``remove_post`` is idempotent;
  ``update_engagement`` requires the post to already exist.
"""

from __future__ import annotations

import pytest

from litemiro.feed.engine import FeedEngine
from litemiro.interfaces import FeedEngineLike
from litemiro.models import Agent, Post
from litemiro.social.graph import SocialGraph


@pytest.fixture
def social() -> SocialGraph:
    return SocialGraph()


@pytest.fixture
def feed(social: SocialGraph) -> FeedEngine:
    return FeedEngine(social=social)


def _post(
    post_id: str,
    author: str,
    *,
    content: str = "x",
    topics: tuple[str, ...] = ("ai",),
    created_round: int = 0,
    likes: int = 0,
    reposts: int = 0,
    quotes: int = 0,
) -> Post:
    return Post(
        post_id=post_id,
        author_id=author,
        content=content,
        topics=topics,
        created_round=created_round,
        likes=likes,
        reposts=reposts,
        quotes=quotes,
    )


def _agent(agent_id: str = "me", interests: tuple[str, ...] = ("ai",)) -> Agent:
    return Agent(agent_id=agent_id, interests=interests)


class TestEmpty:
    def test_empty_index_returns_empty_feed(self, feed: FeedEngine) -> None:
        assert feed.build_feed(agent=_agent(), current_round=0) == ()


class TestCandidacy:
    def test_following_author_post_is_included(self, feed: FeedEngine, social: SocialGraph) -> None:
        social.follow("me", "alice")
        feed.index_post(_post("p1", "alice", topics=("politics",)))
        result = feed.build_feed(agent=_agent("me", interests=()), current_round=1)
        assert [p.post_id for p in result] == ["p1"]

    def test_topic_match_post_is_included(self, feed: FeedEngine) -> None:
        feed.index_post(_post("p1", "stranger", topics=("ai",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert [p.post_id for p in result] == ["p1"]

    def test_no_match_post_is_excluded(self, feed: FeedEngine) -> None:
        feed.index_post(_post("p1", "stranger", topics=("politics",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert result == ()

    def test_self_authored_post_is_excluded(self, feed: FeedEngine) -> None:
        feed.index_post(_post("p1", "me", topics=("ai",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert result == ()

    def test_post_in_both_pools_dedup(self, feed: FeedEngine, social: SocialGraph) -> None:
        social.follow("me", "alice")
        feed.index_post(_post("p1", "alice", topics=("ai",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert [p.post_id for p in result] == ["p1"]


class TestRanking:
    def test_hot_score_descending(self, feed: FeedEngine) -> None:
        feed.index_post(_post("low", "a", likes=1))
        feed.index_post(_post("hi", "a", likes=10))
        result = feed.build_feed(agent=_agent(), current_round=1)
        assert [p.post_id for p in result] == ["hi", "low"]

    def test_tie_breaks_by_post_id_ascending(self, feed: FeedEngine) -> None:
        feed.index_post(_post("p_b", "a", likes=5))
        feed.index_post(_post("p_a", "a", likes=5))
        result = feed.build_feed(agent=_agent(), current_round=1)
        assert [p.post_id for p in result] == ["p_a", "p_b"]


class TestLimit:
    def test_default_limit_is_twenty(self, feed: FeedEngine) -> None:
        for i in range(25):
            feed.index_post(_post(f"p{i:02d}", "a", likes=i))
        result = feed.build_feed(agent=_agent(), current_round=1)
        assert len(result) == 20

    def test_explicit_limit(self, feed: FeedEngine) -> None:
        for i in range(10):
            feed.index_post(_post(f"p{i:02d}", "a", likes=i))
        result = feed.build_feed(agent=_agent(), current_round=1, limit=3)
        assert len(result) == 3

    def test_limit_zero_returns_empty(self, feed: FeedEngine) -> None:
        feed.index_post(_post("p1", "a"))
        result = feed.build_feed(agent=_agent(), current_round=1, limit=0)
        assert result == ()

    def test_limit_negative_rejected(self, feed: FeedEngine) -> None:
        with pytest.raises(ValueError, match="limit"):
            feed.build_feed(agent=_agent(), current_round=1, limit=-1)


class TestEngagementUpdate:
    def test_update_changes_ranking(self, feed: FeedEngine) -> None:
        feed.index_post(_post("p1", "a", likes=1))
        feed.index_post(_post("p2", "a", likes=10))
        before = feed.build_feed(agent=_agent(), current_round=1)
        assert [p.post_id for p in before] == ["p2", "p1"]
        feed.update_engagement(_post("p1", "a", likes=100))
        after = feed.build_feed(agent=_agent(), current_round=1)
        assert [p.post_id for p in after] == ["p1", "p2"]

    def test_update_unknown_post_raises(self, feed: FeedEngine) -> None:
        with pytest.raises(KeyError, match="p1"):
            feed.update_engagement(_post("p1", "a"))

    def test_index_duplicate_rejected(self, feed: FeedEngine) -> None:
        feed.index_post(_post("p1", "a"))
        with pytest.raises(ValueError, match="p1"):
            feed.index_post(_post("p1", "a"))


class TestRemove:
    def test_remove_makes_post_disappear(self, feed: FeedEngine) -> None:
        feed.index_post(_post("p1", "a"))
        feed.remove_post("p1")
        assert feed.build_feed(agent=_agent(), current_round=1) == ()

    def test_remove_unknown_post_is_noop(self, feed: FeedEngine) -> None:
        feed.remove_post("ghost")

    def test_remove_drops_topic_index_entry(self, feed: FeedEngine) -> None:
        feed.index_post(_post("p1", "a", topics=("ai",)))
        feed.remove_post("p1")
        # Re-indexing the same id must succeed (would fail if topic
        # index still held a stale reference and triggered duplicate
        # logic on re-add).
        feed.index_post(_post("p1", "a", topics=("ai",)))
        result = feed.build_feed(agent=_agent(), current_round=1)
        assert [p.post_id for p in result] == ["p1"]


class TestDeterminism:
    def test_repeated_build_feed_identical(self, feed: FeedEngine) -> None:
        for i in range(5):
            feed.index_post(_post(f"p{i}", "a", likes=i))
        first = feed.build_feed(agent=_agent(), current_round=1)
        second = feed.build_feed(agent=_agent(), current_round=1)
        assert first == second


def test_protocol_is_satisfied(feed: FeedEngine) -> None:
    assert isinstance(feed, FeedEngineLike)
