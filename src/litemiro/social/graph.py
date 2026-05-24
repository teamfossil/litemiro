"""``SocialGraph`` — agent follow-graph component owned by **B**.

The Notion design doc only states that this component must support
"팔로우/언팔로우, 순환 참조 방지, 직렬화/역직렬화" (Phase 2 Section 9.2). The
contract pinned here — and exercised by ``tests/unit/test_social_graph``
— is:

* **self-follow is rejected** (the only "순환 참조" we forbid; mutual
  follow A→B and B→A is allowed, matching common SNS semantics).
* :meth:`follow` and :meth:`unfollow` are **idempotent**.
* :meth:`to_dict` is **deterministic**: outer keys sorted, value lists
  sorted, and users with no following are omitted (so that a freshly
  unfollowed user does not leave an empty list behind).
* :meth:`from_dict` is the inverse — it accepts unsorted input but
  rejects records that would re-introduce self-follow.

Internal storage keeps ``following`` and ``followers`` indexes in sync
so all O(1) lookups (``followers``, ``following``, ``*_count``) remain
flat regardless of graph size.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping


class SocialGraph:
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
    def from_dict(cls, data: Mapping[str, Iterable[str]]) -> SocialGraph:
        graph = cls()
        for follower, followees in data.items():
            for followee in followees:
                graph.follow(follower, followee)
        return graph
