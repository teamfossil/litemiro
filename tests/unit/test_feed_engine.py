"""TDD spec for ``litemiro.feed.engine.FeedEngine``.

Notion Section 3.2 says only "FeedEngine → SocialGraph: 팔로우 관계 기반 피드
필터링" and Section 9.2 only lists generic test-points. B locks the contract:

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
from litemiro.interfaces import EmbedderLike, FeedEngineLike
from litemiro.models import Agent, Post
from litemiro.social.graph import SocialGraph


class _FakeEmbedder:
    """Deterministic dimensional embedder for unit tests."""

    def __init__(self, mapping: dict[str, tuple[float, ...]]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def embed(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        if text not in self._mapping:
            raise KeyError(f"no fixture vector for {text!r}")
        return self._mapping[text]


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

    def test_update_rejects_changed_topics(self, feed: FeedEngine) -> None:
        # Regression: a caller that re-publishes a post with mutated
        # ``topics`` must not silently desync ``_topic_index`` /
        # ``_topic_embeddings`` from the stored snapshot.
        feed.index_post(_post("p1", "a", topics=("ai",)))
        with pytest.raises(ValueError, match="immutable"):
            feed.update_engagement(_post("p1", "a", topics=("politics",), likes=5))

    def test_update_accepts_reordered_topics(self, feed: FeedEngine) -> None:
        # ``topics`` is identity, but compared as a set — the inverted
        # index keys on membership only. Re-publishing the same topic
        # set in a different order is an engagement-only update, not a
        # mutation, so the immutability guard must not fire.
        feed.index_post(_post("p1", "a", topics=("ai", "politics")))
        feed.update_engagement(_post("p1", "a", topics=("politics", "ai"), likes=5))
        result = feed.build_feed(agent=_agent(interests=("ai",)), current_round=1)
        assert [p.post_id for p in result] == ["p1"]
        assert result[0].likes == 5

    def test_update_rejects_changed_author(self, feed: FeedEngine) -> None:
        # Regression: changing ``author_id`` would break follow-graph
        # candidacy because ``build_feed`` reads it from the stored post.
        feed.index_post(_post("p1", "a", topics=("ai",)))
        with pytest.raises(ValueError, match="immutable"):
            feed.update_engagement(_post("p1", "b", topics=("ai",), likes=5))

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


class TestSemanticMatching:
    """Notion Section 3.2 candidacy includes semantic interest-topic similarity.

    Without an embedder ``FeedEngine`` keeps the W2 default behaviour
    (exact topic match only). When an ``EmbedderLike`` is injected,
    posts whose topics are *cosine-similar* to any of the agent's
    interests above the threshold also enter the candidate pool.
    """

    def test_no_embedder_keeps_exact_match_default(self, social: SocialGraph) -> None:
        feed = FeedEngine(social=social)
        feed.index_post(_post("p1", "stranger", topics=("ml",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert result == ()

    def test_high_similarity_topic_is_included(self, social: SocialGraph) -> None:
        # "ai" and "ml" are nearly co-linear -> cosine ~ 0.99
        embedder = _FakeEmbedder({"ai": (1.0, 0.0), "ml": (0.99, 0.14)})
        feed = FeedEngine(social=social, embedder=embedder, similarity_threshold=0.5)
        feed.index_post(_post("p1", "stranger", topics=("ml",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert [p.post_id for p in result] == ["p1"]

    def test_low_similarity_topic_is_excluded(self, social: SocialGraph) -> None:
        # orthogonal vectors -> cosine = 0
        embedder = _FakeEmbedder({"ai": (1.0, 0.0), "politics": (0.0, 1.0)})
        feed = FeedEngine(social=social, embedder=embedder, similarity_threshold=0.5)
        feed.index_post(_post("p1", "stranger", topics=("politics",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert result == ()

    def test_threshold_is_inclusive(self, social: SocialGraph) -> None:
        # cosine == threshold should match (>= comparison)
        embedder = _FakeEmbedder({"ai": (1.0, 0.0), "x": (0.5, 0.866)})
        feed = FeedEngine(social=social, embedder=embedder, similarity_threshold=0.5)
        feed.index_post(_post("p1", "stranger", topics=("x",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert [p.post_id for p in result] == ["p1"]

    def test_exact_match_does_not_require_embedder(self, social: SocialGraph) -> None:
        # Exact topic equality must always match — even when the
        # embedder claims zero similarity.
        embedder = _FakeEmbedder({"ai": (1.0, 0.0)})
        feed = FeedEngine(social=social, embedder=embedder, similarity_threshold=0.99)
        feed.index_post(_post("p1", "stranger", topics=("ai",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert [p.post_id for p in result] == ["p1"]

    def test_dedup_when_post_matches_via_both_paths(self, social: SocialGraph) -> None:
        # If a post is reached via following AND via semantic match the
        # candidate pool deduplicates by post_id.
        social.follow("me", "alice")
        embedder = _FakeEmbedder({"ai": (1.0, 0.0), "ml": (0.99, 0.14)})
        feed = FeedEngine(social=social, embedder=embedder, similarity_threshold=0.5)
        feed.index_post(_post("p1", "alice", topics=("ml",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert [p.post_id for p in result] == ["p1"]

    def test_self_authored_still_excluded_under_semantic(self, social: SocialGraph) -> None:
        embedder = _FakeEmbedder({"ai": (1.0, 0.0), "ml": (0.99, 0.14)})
        feed = FeedEngine(social=social, embedder=embedder, similarity_threshold=0.5)
        feed.index_post(_post("p1", "me", topics=("ml",)))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert result == ()

    def test_topic_embedding_cached(self, social: SocialGraph) -> None:
        # A topic seen during index_post must not be re-embedded on
        # build_feed — embeddings are the expensive bit.
        embedder = _FakeEmbedder({"ai": (1.0, 0.0), "ml": (0.99, 0.14)})
        feed = FeedEngine(social=social, embedder=embedder, similarity_threshold=0.5)
        feed.index_post(_post("p1", "stranger", topics=("ml",)))
        feed.index_post(_post("p2", "stranger", topics=("ml",)))  # same topic
        embedder.calls.clear()
        feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        # Each topic should be embedded at most once per build_feed call:
        # one for "ai" (the interest), nothing for "ml" (cached).
        assert embedder.calls.count("ml") == 0
        assert embedder.calls.count("ai") == 1

    def test_negative_threshold_rejected(self, social: SocialGraph) -> None:
        embedder = _FakeEmbedder({})
        with pytest.raises(ValueError, match="similarity_threshold"):
            FeedEngine(social=social, embedder=embedder, similarity_threshold=-0.1)

    def test_threshold_above_one_rejected(self, social: SocialGraph) -> None:
        embedder = _FakeEmbedder({})
        with pytest.raises(ValueError, match="similarity_threshold"):
            FeedEngine(social=social, embedder=embedder, similarity_threshold=1.5)


class TestTopicHierarchy:
    """Post-MVP — Issue #18 topic_hierarchy ranking boost.

    When ``topic_hierarchy`` (child→parent) is injected, posts whose
    topic's parent matches an agent interest enter the candidate pool
    (a third path next to direct/cosine), and per-post ranking score is
    boosted by match kind: direct > parent > cosine. When the hierarchy
    is omitted the engine keeps its W2 default behaviour bit-for-bit.
    """

    def test_no_hierarchy_keeps_default_ranking(self, social: SocialGraph) -> None:
        feed = FeedEngine(social=social)
        feed.index_post(_post("p1", "stranger", topics=("ai",), likes=1))
        feed.index_post(_post("p2", "stranger", topics=("ai",), likes=5))
        result = feed.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert [p.post_id for p in result] == ["p2", "p1"]

    def test_hierarchy_equivalent_to_none_when_no_parent_matches(self, social: SocialGraph) -> None:
        without = FeedEngine(social=social)
        without.index_post(_post("p1", "stranger", topics=("ai",), likes=2))
        without.index_post(_post("p2", "stranger", topics=("ai",), likes=1))
        baseline = without.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)

        with_hier = FeedEngine(social=social, topic_hierarchy={"ml": "tech", "robotics": "tech"})
        with_hier.index_post(_post("p1", "stranger", topics=("ai",), likes=2))
        with_hier.index_post(_post("p2", "stranger", topics=("ai",), likes=1))
        # Interests don't intersect the hierarchy at all → identical
        # ordering AND identical scores.
        boosted = with_hier.build_feed(agent=_agent("me", interests=("ai",)), current_round=1)
        assert [p.post_id for p in baseline] == [p.post_id for p in boosted]

    def test_parent_topic_enters_candidate_pool(self, social: SocialGraph) -> None:
        feed = FeedEngine(social=social, topic_hierarchy={"ml": "tech"})
        feed.index_post(_post("p1", "stranger", topics=("ml",)))
        # "tech" is the agent's interest; "ml" is a child of "tech".
        result = feed.build_feed(agent=_agent("me", interests=("tech",)), current_round=1)
        assert [p.post_id for p in result] == ["p1"]

    def test_parent_path_requires_hierarchy(self, social: SocialGraph) -> None:
        feed = FeedEngine(social=social)  # no hierarchy
        feed.index_post(_post("p1", "stranger", topics=("ml",)))
        result = feed.build_feed(agent=_agent("me", interests=("tech",)), current_round=1)
        assert result == ()

    def test_direct_beats_parent_in_ranking(self, social: SocialGraph) -> None:
        feed = FeedEngine(social=social, topic_hierarchy={"ml": "tech"})
        feed.index_post(_post("p_direct", "alice", topics=("tech",), likes=0))
        feed.index_post(_post("p_parent", "bob", topics=("ml",), likes=0))
        # Both age=1 hot_score=0. Direct match weight (1.0) > parent (0.5).
        result = feed.build_feed(agent=_agent("me", interests=("tech",)), current_round=1)
        assert [p.post_id for p in result] == ["p_direct", "p_parent"]

    def test_parent_beats_cosine_in_ranking(self, social: SocialGraph) -> None:
        embedder = _FakeEmbedder({"tech": (1.0, 0.0), "ml": (1.0, 0.0), "x": (0.9, 0.44)})
        feed = FeedEngine(
            social=social,
            embedder=embedder,
            similarity_threshold=0.5,
            topic_hierarchy={"ml": "tech"},
        )
        feed.index_post(_post("p_parent", "alice", topics=("ml",), likes=0))
        feed.index_post(_post("p_cosine", "bob", topics=("x",), likes=0))
        # Parent (ml→tech) boost 0.5 > cosine boost 0.25, both at hot_score=0.
        result = feed.build_feed(agent=_agent("me", interests=("tech",)), current_round=1)
        assert [p.post_id for p in result] == ["p_parent", "p_cosine"]

    def test_zero_weights_yields_no_boost(self, social: SocialGraph) -> None:
        feed = FeedEngine(
            social=social,
            topic_hierarchy={"ml": "tech"},
            direct_match_weight=0.0,
            parent_match_weight=0.0,
            cosine_match_weight=0.0,
        )
        feed.index_post(_post("p_parent", "alice", topics=("ml",), likes=1))
        feed.index_post(_post("p_direct", "bob", topics=("tech",), likes=2))
        # Pure hot_score: p_direct (2) > p_parent (1). Boost contributions
        # cancel to zero so ranking matches the no-hierarchy baseline.
        result = feed.build_feed(agent=_agent("me", interests=("tech",)), current_round=1)
        assert [p.post_id for p in result] == ["p_direct", "p_parent"]

    def test_large_parent_weight_overrides_hot_score(self, social: SocialGraph) -> None:
        feed = FeedEngine(
            social=social,
            topic_hierarchy={"ml": "tech"},
            direct_match_weight=0.0,
            parent_match_weight=10.0,
        )
        feed.index_post(_post("p_parent", "alice", topics=("ml",), likes=0))
        feed.index_post(_post("p_direct", "bob", topics=("tech",), likes=5))
        # Direct's hot_score (5/2^1.5 ≈ 1.77) loses to parent boost 10.0.
        result = feed.build_feed(agent=_agent("me", interests=("tech",)), current_round=1)
        assert [p.post_id for p in result] == ["p_parent", "p_direct"]

    def test_unrelated_parent_does_not_match(self, social: SocialGraph) -> None:
        feed = FeedEngine(social=social, topic_hierarchy={"ml": "tech"})
        feed.index_post(_post("p1", "stranger", topics=("ml",)))
        # "politics" interest has no relation to "tech".
        result = feed.build_feed(agent=_agent("me", interests=("politics",)), current_round=1)
        assert result == ()

    def test_self_authored_still_excluded_under_hierarchy(self, social: SocialGraph) -> None:
        feed = FeedEngine(social=social, topic_hierarchy={"ml": "tech"})
        feed.index_post(_post("p1", "me", topics=("ml",)))
        result = feed.build_feed(agent=_agent("me", interests=("tech",)), current_round=1)
        assert result == ()

    def test_direct_match_takes_precedence_over_parent_when_post_has_both(
        self, social: SocialGraph
    ) -> None:
        feed = FeedEngine(
            social=social,
            topic_hierarchy={"ml": "tech"},
            direct_match_weight=1.0,
            parent_match_weight=0.5,
        )
        # post has both "tech" (direct) and "ml" (parent→tech). Direct
        # wins → boost = 1.0, not 0.5.
        feed.index_post(_post("p_both", "alice", topics=("tech", "ml"), likes=0))
        feed.index_post(_post("p_parent_only", "bob", topics=("ml",), likes=0))
        result = feed.build_feed(agent=_agent("me", interests=("tech",)), current_round=1)
        assert [p.post_id for p in result] == ["p_both", "p_parent_only"]

    def test_negative_weight_rejected(self, social: SocialGraph) -> None:
        with pytest.raises(ValueError, match="direct_match_weight"):
            FeedEngine(social=social, direct_match_weight=-0.1)
        with pytest.raises(ValueError, match="parent_match_weight"):
            FeedEngine(social=social, parent_match_weight=-0.1)
        with pytest.raises(ValueError, match="cosine_match_weight"):
            FeedEngine(social=social, cosine_match_weight=-0.1)

    def test_hierarchy_is_copied_not_aliased(self, social: SocialGraph) -> None:
        # Mutating the caller's mapping after construction must not
        # silently change FeedEngine's expansion behaviour.
        h = {"ml": "tech"}
        feed = FeedEngine(social=social, topic_hierarchy=h)
        h["robotics"] = "tech"  # post-construction mutation
        feed.index_post(_post("p1", "stranger", topics=("robotics",)))
        result = feed.build_feed(agent=_agent("me", interests=("tech",)), current_round=1)
        # robotics→tech was added AFTER construction, so it must NOT
        # have been picked up.
        assert result == ()

    def test_determinism_with_hierarchy(self, social: SocialGraph) -> None:
        feed = FeedEngine(social=social, topic_hierarchy={"ml": "tech", "nlp": "tech"})
        for i, topic in enumerate(("ml", "tech", "nlp", "tech")):
            feed.index_post(_post(f"p{i}", "stranger", topics=(topic,)))
        agent = _agent("me", interests=("tech",))
        first = feed.build_feed(agent=agent, current_round=1)
        for _ in range(3):
            assert feed.build_feed(agent=agent, current_round=1) == first


def test_protocol_is_satisfied(feed: FeedEngine) -> None:
    assert isinstance(feed, FeedEngineLike)


def test_fake_embedder_satisfies_protocol() -> None:
    assert isinstance(_FakeEmbedder({}), EmbedderLike)
