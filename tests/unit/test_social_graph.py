"""TDD spec for ``litemiro.social.graph.SocialGraph``.

Notion 9.2 only says: "팔로우/언팔로우, 순환 참조 방지, 직렬화/역직렬화".
B locks the meaning here so A/C can rely on it:

* "순환 참조 방지" = **self-follow** is rejected. Mutual follow (A→B and
  B→A) is allowed — Twitter-style.
* ``follow`` and ``unfollow`` are idempotent.
* ``to_dict`` is **deterministic**: outer keys sorted, value lists
  sorted, and users with no following are omitted.
* ``from_dict`` is the inverse and rejects malformed input
  (e.g. self-follow records).
"""

from __future__ import annotations

import pytest

from litemiro.interfaces import SocialGraphLike
from litemiro.social.graph import SocialGraph


class TestEmpty:
    def test_followers_following_empty(self) -> None:
        g = SocialGraph()
        assert g.followers("a") == frozenset()
        assert g.following("a") == frozenset()
        assert g.follower_count("a") == 0
        assert g.following_count("a") == 0

    def test_to_dict_empty(self) -> None:
        assert SocialGraph().to_dict() == {}


class TestFollow:
    def test_single_follow_updates_both_sides(self) -> None:
        g = SocialGraph()
        g.follow("a", "b")
        assert g.following("a") == frozenset({"b"})
        assert g.followers("b") == frozenset({"a"})
        assert g.follower_count("b") == 1
        assert g.following_count("a") == 1

    def test_self_follow_is_rejected(self) -> None:
        g = SocialGraph()
        with pytest.raises(ValueError, match="self"):
            g.follow("a", "a")

    def test_follow_is_idempotent(self) -> None:
        g = SocialGraph()
        g.follow("a", "b")
        g.follow("a", "b")
        assert g.follower_count("b") == 1
        assert g.following_count("a") == 1

    def test_mutual_follow_allowed(self) -> None:
        g = SocialGraph()
        g.follow("a", "b")
        g.follow("b", "a")
        assert g.following("a") == frozenset({"b"})
        assert g.following("b") == frozenset({"a"})
        assert g.followers("a") == frozenset({"b"})
        assert g.followers("b") == frozenset({"a"})

    def test_multiple_followers_for_one_target(self) -> None:
        g = SocialGraph()
        for follower in ("a", "b", "c"):
            g.follow(follower, "x")
        assert g.followers("x") == frozenset({"a", "b", "c"})
        assert g.follower_count("x") == 3


class TestUnfollow:
    def test_unfollow_removes_relation_both_sides(self) -> None:
        g = SocialGraph()
        g.follow("a", "b")
        g.unfollow("a", "b")
        assert g.following("a") == frozenset()
        assert g.followers("b") == frozenset()

    def test_unfollow_unknown_relation_is_noop(self) -> None:
        g = SocialGraph()
        g.unfollow("a", "b")
        g.follow("a", "b")
        g.unfollow("a", "c")
        assert g.following("a") == frozenset({"b"})

    def test_unfollow_keeps_other_relations(self) -> None:
        g = SocialGraph()
        g.follow("a", "b")
        g.follow("a", "c")
        g.unfollow("a", "b")
        assert g.following("a") == frozenset({"c"})
        assert g.followers("b") == frozenset()

    def test_unfollow_then_to_dict_drops_user(self) -> None:
        g = SocialGraph()
        g.follow("a", "b")
        g.unfollow("a", "b")
        assert g.to_dict() == {}


class TestImmutability:
    def test_followers_returns_frozenset(self) -> None:
        g = SocialGraph()
        g.follow("a", "b")
        # frozenset is immutable; mutating the internal store after
        # calling .followers() must not affect the snapshot we took.
        snap = g.followers("b")
        g.follow("c", "b")
        assert snap == frozenset({"a"})

    def test_following_returns_frozenset(self) -> None:
        g = SocialGraph()
        g.follow("a", "b")
        snap = g.following("a")
        g.follow("a", "c")
        assert snap == frozenset({"b"})


class TestSerialization:
    def test_to_dict_sorts_keys_and_values(self) -> None:
        g = SocialGraph()
        g.follow("c", "z")
        g.follow("a", "n")
        g.follow("a", "m")
        snap = g.to_dict()
        assert list(snap.keys()) == ["a", "c"]
        assert snap["a"] == ["m", "n"]
        assert snap["c"] == ["z"]

    def test_to_dict_omits_followers_with_no_following(self) -> None:
        g = SocialGraph()
        g.follow("a", "b")
        snap = g.to_dict()
        assert "b" not in snap

    def test_to_dict_is_deterministic(self) -> None:
        g = SocialGraph()
        for follower, followee in [("a", "b"), ("c", "b"), ("a", "d")]:
            g.follow(follower, followee)
        assert g.to_dict() == g.to_dict()

    def test_round_trip_preserves_relations(self) -> None:
        original = SocialGraph()
        original.follow("a", "b")
        original.follow("a", "c")
        original.follow("b", "a")
        snap = original.to_dict()
        rehydrated = SocialGraph.from_dict(snap)
        assert rehydrated.to_dict() == snap
        assert rehydrated.followers("b") == frozenset({"a"})
        assert rehydrated.following("b") == frozenset({"a"})

    def test_from_dict_empty_graph(self) -> None:
        assert SocialGraph.from_dict({}).to_dict() == {}

    def test_from_dict_rejects_self_follow(self) -> None:
        with pytest.raises(ValueError, match="self"):
            SocialGraph.from_dict({"a": ["a"]})

    def test_from_dict_accepts_unsorted_input(self) -> None:
        # to_dict outputs sorted, but from_dict should not require it.
        g = SocialGraph.from_dict({"a": ["z", "m", "b"]})
        assert g.following("a") == frozenset({"z", "m", "b"})
        # And re-serialising sorts again.
        assert g.to_dict()["a"] == ["b", "m", "z"]

    def test_to_dict_byte_snapshot(self) -> None:
        """Pin the exact serialization shape so accidental drift is caught.

        ``to_dict`` is the wire format C consumes for Phase 3. A drift in
        key ordering, value ordering, or empty-bucket handling would
        silently break replays — the snapshot below freezes the contract.
        """
        g = SocialGraph()
        for follower, followee in [
            ("alice", "carol"),
            ("alice", "bob"),
            ("bob", "alice"),
            ("dave", "bob"),  # dave will be unfollowed -> must not leak
        ]:
            g.follow(follower, followee)
        g.unfollow("dave", "bob")
        assert g.to_dict() == {
            "alice": ["bob", "carol"],
            "bob": ["alice"],
        }


def test_protocol_is_satisfied() -> None:
    assert isinstance(SocialGraph(), SocialGraphLike)
