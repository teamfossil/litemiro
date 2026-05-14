"""``FeedEngine`` — owned by **B**.

Maintains a topic inverted index and the live snapshot of posts so
``build_feed`` is O(K log K) on the candidate pool rather than O(N) on
the full corpus. The contract pinned by the unit suite:

* **Candidate pool** = union of three paths — (1) ``following`` author's
  posts, (2) posts whose ``topics`` intersect ``agent.interests``
  exactly, and (3) when an ``EmbedderLike`` is injected, posts whose
  topic is cosine-similar to an interest at or above
  ``similarity_threshold``. Posts authored by the agent itself are
  excluded.
* **Ranking** = ``Post.hot_score(current_round)`` descending,
  ``post_id`` ascending tie-break → fully deterministic.
* ``index_post`` rejects duplicate ids; ``update_engagement`` rejects
  unknown ids; ``remove_post`` is idempotent.
* Topic embeddings are computed at ``index_post`` time and cached so a
  single ``build_feed`` call costs at most one ``embed`` per interest,
  not one per (interest, topic) pair.

The ``SocialGraphLike`` dependency is supplied at construction time.
The Protocol in ``litemiro.interfaces`` deliberately omits this — it's
an implementation detail of B's wiring, not part of the public surface.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

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
    ) -> None:
        if not 0.0 <= similarity_threshold <= 1.0:
            raise ValueError(
                f"similarity_threshold must be in [0.0, 1.0], got {similarity_threshold}"
            )
        self._social = social
        self._embedder = embedder
        self._similarity_threshold = similarity_threshold
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
        # from the stored snapshot.
        if post.author_id != existing.author_id or post.topics != existing.topics:
            raise ValueError(
                "update_engagement may only change engagement counters; "
                "author_id and topics are immutable"
            )
        self._posts[post.post_id] = post

    def build_feed(self, *, agent: Agent, current_round: int, limit: int = 20) -> tuple[Post, ...]:
        if limit < 0:
            raise ValueError(f"limit must be non-negative, got {limit}")
        if limit == 0:
            return ()

        following = self._social.following(agent.agent_id)

        candidate_ids: set[str] = set()
        for topic in agent.interests:
            candidate_ids |= self._topic_index.get(topic, set())
        if self._embedder is not None and agent.interests:
            interest_vectors = tuple(self._embedder.embed(i) for i in agent.interests)
            interests = frozenset(agent.interests)
            for topic, ids in self._topic_index.items():
                if topic in interests:
                    continue
                topic_vec = self._topic_embeddings.get(topic)
                if topic_vec is None:
                    continue
                if any(
                    _cosine(iv, topic_vec) >= self._similarity_threshold for iv in interest_vectors
                ):
                    candidate_ids |= ids
        for post_id, post in self._posts.items():
            if post.author_id in following:
                candidate_ids.add(post_id)

        candidates = [
            self._posts[pid]
            for pid in candidate_ids
            if self._posts[pid].author_id != agent.agent_id
        ]
        candidates.sort(key=lambda p: (-p.hot_score(current_round), p.post_id))
        return tuple(candidates[:limit])


def _cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (norm_a * norm_b)
