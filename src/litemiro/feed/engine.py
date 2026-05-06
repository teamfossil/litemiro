"""``FeedEngine`` — owned by **B**.

Maintains a topic inverted index and the live snapshot of posts so
``build_feed`` is O(K log K) on the candidate pool rather than O(N) on
the full corpus. The contract pinned by the unit suite:

* **Candidate pool** = union of (``following`` author's posts) and
  (topic-match posts where one of the post's topics is in
  ``agent.interests``). Posts authored by the agent itself are excluded.
* **Ranking** = ``Post.hot_score(current_round)`` descending,
  ``post_id`` ascending tie-break → fully deterministic.
* ``index_post`` rejects duplicate ids; ``update_engagement`` rejects
  unknown ids; ``remove_post`` is idempotent.

The ``SocialGraphLike`` dependency is supplied at construction time.
The Protocol in ``litemiro.interfaces`` deliberately omits this — it's
an implementation detail of B's wiring, not part of the public surface.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from litemiro.models import Agent, Post

if TYPE_CHECKING:
    from litemiro.interfaces import SocialGraphLike


class FeedEngine:
    def __init__(self, *, social: SocialGraphLike) -> None:
        self._social = social
        self._posts: dict[str, Post] = {}
        # topic -> post_ids (set so add/remove stay O(1))
        self._topic_index: dict[str, set[str]] = {}

    def index_post(self, post: Post) -> None:
        if post.post_id in self._posts:
            raise ValueError(f"post already indexed: {post.post_id}")
        self._posts[post.post_id] = post
        for topic in post.topics:
            self._topic_index.setdefault(topic, set()).add(post.post_id)

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

    def update_engagement(self, post: Post) -> None:
        if post.post_id not in self._posts:
            raise KeyError(f"unknown post_id: {post.post_id}")
        # Topics are part of the immutable identity of a post — only
        # engagement counters move. We keep the topic index intact.
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
