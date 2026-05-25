"""``FeedEngine`` — owned by **B**.

Maintains a topic inverted index and the live snapshot of posts. The
contract pinned by the unit suite:

* **Candidate pool** = union of three paths — (1) ``following`` author's
  posts, (2) posts whose ``topics`` intersect ``agent.interests``
  exactly, and (3) when an ``EmbedderLike`` is injected, posts whose
  topic is cosine-similar to an interest at or above
  ``similarity_threshold``. Posts authored by the agent itself are
  excluded.
* **Ranking** = ``Post.hot_score(current_round)`` descending,
  ``post_id`` ascending tie-break → fully deterministic.
* ``index_post`` rejects duplicate ids; ``update_engagement`` rejects
  unknown ids and any mutation of ``author_id``/``topics`` (only
  engagement counters are mutable); ``remove_post`` is idempotent.
* Topic embeddings are computed at ``index_post`` time and cached so a
  single ``build_feed`` call costs at most one ``embed`` per interest,
  not one per (interest, topic) pair.

**Complexity**: the topic-intersection and embedding-similarity paths
are served by the inverted index — cost scales with the number of
*matching* topics, not the corpus. The follow path currently scans
``self._posts`` (O(N) in the live corpus); if N grows large enough to
matter we can mirror an author→post_ids index, but at expected Phase 2
scale the constant on a dict iteration is well under the LLM latency
budget, so we leave the cleaner code. Final ranking is O(K log K)
where K is the candidate-pool size.

The ``SocialGraphLike`` dependency is supplied at construction time.
The Protocol in ``litemiro.interfaces`` deliberately omits this — it's
an implementation detail of B's wiring, not part of the public surface.

Post-MVP — when ``topic_hierarchy`` (child→parent) is provided at
construction time, posts whose topic's parent matches an agent interest
also enter the candidate pool and the ranking score is boosted by the
match kind (direct > parent > cosine). Weights are opt-in: with
``topic_hierarchy=None`` the engine keeps the W2-default behaviour
(pure ``hot_score`` ranking, no parent expansion). Issue #18.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING

from litemiro._vector import cosine
from litemiro.models import Agent, Post

if TYPE_CHECKING:
    from litemiro.interfaces import EmbedderLike, SocialGraphLike


class FeedEngine:
    def __init__(
        self,
        *,
        social: SocialGraphLike,
        embedder: EmbedderLike | None = None,
        similarity_threshold: float = 0.4,
        topic_hierarchy: Mapping[str, str] | None = None,
        direct_match_weight: float = 1.0,
        parent_match_weight: float = 0.5,
        cosine_match_weight: float = 0.25,
    ) -> None:
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError(
                f"similarity_threshold must be in [0.0, 1.0], got {similarity_threshold}"
            )
        for name, weight in (
            ("direct_match_weight", direct_match_weight),
            ("parent_match_weight", parent_match_weight),
            ("cosine_match_weight", cosine_match_weight),
        ):
            if weight < 0:
                raise ValueError(f"{name} must be non-negative, got {weight}")
        self._social = social
        self._embedder = embedder
        self._similarity_threshold = similarity_threshold
        self._topic_hierarchy: dict[str, str] | None = (
            dict(topic_hierarchy) if topic_hierarchy is not None else None
        )
        self._direct_match_weight = direct_match_weight
        self._parent_match_weight = parent_match_weight
        self._cosine_match_weight = cosine_match_weight
        self._posts: dict[str, Post] = {}
        self._topic_index: dict[str, set[str]] = {}
        self._topic_embeddings: dict[str, tuple[float, ...]] = {}

    def index_post(self, post: Post) -> None:
        if post.post_id in self._posts:
            raise ValueError(f"post already indexed: {post.post_id}")
        self._posts[post.post_id] = post
        for topic in post.topics:
            self._topic_index.setdefault(topic, set()).add(post.post_id)
            if self._embedder is not None and topic not in self._topic_embeddings:
                self._topic_embeddings[topic] = self._embedder.embed(topic)

    def remove_post(self, post_id: str) -> None:
        post = self._posts.pop(post_id, None)
        if post is None:
            return
        for topic in post.topics:
            ids = self._topic_index.get(topic)
            if ids is None:
                continue
            ids.discard(post_id)
            if not ids:
                del self._topic_index[topic]
                self._topic_embeddings.pop(topic, None)

    def update_engagement(self, post: Post) -> None:
        existing = self._posts.get(post.post_id)
        if existing is None:
            raise KeyError(f"unknown post_id: {post.post_id}")
        # Topics and author_id are part of the immutable identity of a
        # post — only engagement counters may change. Enforce that here
        # so the topic index and follow-graph candidacy can't desync
        # from the stored snapshot. ``topics`` is compared as a set: the
        # inverted index keys on membership only, so re-publishing the
        # same topics in a different order (or with duplicates) is not a
        # mutation and must not trip this guard.
        topics_changed = frozenset(post.topics) != frozenset(existing.topics)
        if post.author_id != existing.author_id or topics_changed:
            raise ValueError(
                "update_engagement may only change engagement counters; "
                "author_id and topics are immutable"
            )
        self._posts[post.post_id] = post

    def _parent_match_ids(self, interests: frozenset[str]) -> set[str]:
        if self._topic_hierarchy is None or not interests:
            return set()
        out: set[str] = set()
        for topic, ids in self._topic_index.items():
            if topic in interests:
                continue
            parent = self._topic_hierarchy.get(topic)
            if parent is not None and parent in interests:
                out |= ids
        return out

    def _cosine_match_ids(self, interests: frozenset[str]) -> set[str]:
        if self._embedder is None or not interests:
            return set()
        interest_vectors = tuple(self._embedder.embed(i) for i in interests)
        out: set[str] = set()
        for topic, ids in self._topic_index.items():
            if topic in interests:
                continue
            topic_vec = self._topic_embeddings.get(topic)
            if topic_vec is None:
                continue
            if any(cosine(iv, topic_vec) >= self._similarity_threshold for iv in interest_vectors):
                out |= ids
        return out

    def build_feed(self, *, agent: Agent, current_round: int, limit: int = 20) -> tuple[Post, ...]:
        if limit < 0:
            raise ValueError(f"limit must be non-negative, got {limit}")
        if limit == 0:
            return ()

        following = self._social.following(agent.agent_id)
        interests = frozenset(agent.interests)

        direct_ids: set[str] = set()
        for topic in interests:
            direct_ids |= self._topic_index.get(topic, set())

        parent_ids = self._parent_match_ids(interests)
        cosine_ids = self._cosine_match_ids(interests)

        candidate_ids = direct_ids | parent_ids | cosine_ids
        for post_id, post in self._posts.items():
            if post.author_id in following:
                candidate_ids.add(post_id)

        use_hierarchy_boost = self._topic_hierarchy is not None

        def _boost(pid: str) -> float:
            if not use_hierarchy_boost:
                return 0.0
            if pid in direct_ids:
                return self._direct_match_weight
            if pid in parent_ids:
                return self._parent_match_weight
            if pid in cosine_ids:
                return self._cosine_match_weight
            return 0.0

        candidates = [
            self._posts[pid]
            for pid in candidate_ids
            if self._posts[pid].author_id != agent.agent_id
        ]
        candidates.sort(
            key=lambda p: (-(p.hot_score(current_round) + _boost(p.post_id)), p.post_id)
        )
        return tuple(candidates[:limit])
