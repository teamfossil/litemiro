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

Post-MVP — :meth:`add_homophily_edges` augments an already-loaded graph
with ideology-similarity edges for polarization experiments. Issue #19.
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

    def add_homophily_edges(
        self,
        *,
        ideologies: Mapping[str, float],
        threshold: float,
        max_per_agent: int,
    ) -> int:
        """Augment the graph with ideology-similarity edges (post-MVP, Issue #19).

        Distance metric is ``abs(ideo_a - ideo_b)`` — linear, since
        ``AgentProfile.ideology`` is a normalized scalar in ``[0.0, 1.0]``.
        A pair (follower, followee) becomes a candidate when distance is
        at or below ``threshold``. For each follower in sorted order,
        candidates are ranked ``(distance asc, followee_id asc)`` and the
        closest ``max_per_agent`` are added **on top of** the agent's
        existing following — initial ``initial_following`` from Phase 1
        is preserved (this method only adds, never removes), and already-
        followed agents are skipped so repeated calls are idempotent.

        Returns the number of follow edges newly added.

        Behaviour off-by-default — call sites (e.g. an ``OntologyLoader``
        wiring after #13 lands) decide whether to invoke this for a given
        experiment toggle. Agents missing from ``ideologies`` are simply
        ignored (no follower processing, no followee candidacy).
        """
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in [0.0, 1.0], got {threshold}")
        if max_per_agent < 0:
            raise ValueError(f"max_per_agent must be non-negative, got {max_per_agent}")
        for aid, ideo in ideologies.items():
            if not 0.0 <= ideo <= 1.0:
                raise ValueError(f"ideology[{aid!r}] must be in [0.0, 1.0], got {ideo}")

        if max_per_agent == 0 or not ideologies:
            return 0

        added = 0
        for follower in sorted(ideologies):
            ideo_a = ideologies[follower]
            existing = self._following.get(follower, set())
            candidates: list[tuple[float, str]] = []
            for candidate_id, ideo_b in ideologies.items():
                if candidate_id == follower or candidate_id in existing:
                    continue
                distance = abs(ideo_a - ideo_b)
                if distance <= threshold:
                    candidates.append((distance, candidate_id))
            candidates.sort()
            for _, target in candidates[:max_per_agent]:
                self.follow(follower, target)
                added += 1
        return added
