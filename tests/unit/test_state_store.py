from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import pytest
from pydantic import ValidationError

from litemiro.core import StateStore
from litemiro.interfaces import SocialGraphLike, StateStoreLike
from litemiro.models import Agent, Post
from tests.fakes import FakeSocialGraph


def _make_store(
    tmp_path: Path,
    *,
    agents: Iterable[Agent] = (),
    global_seed: int = 0,
    social: FakeSocialGraph | None = None,
) -> StateStore:
    return StateStore(
        agents=agents,
        social=social if social is not None else FakeSocialGraph(),
        social_factory=FakeSocialGraph.from_dict,
        checkpoint_dir=tmp_path / "checkpoints",
        global_seed=global_seed,
    )


class TestConstruction:
    def test_empty_agents_allowed(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.list_agent_ids() == ()

    def test_checkpoint_dir_auto_created(self, tmp_path: Path) -> None:
        nested = tmp_path / "deep" / "nested" / "checkpoints"
        StateStore(
            agents=(),
            social=FakeSocialGraph(),
            social_factory=FakeSocialGraph.from_dict,
            checkpoint_dir=nested,
            global_seed=0,
        )
        assert nested.exists()
        assert nested.is_dir()

    def test_satisfies_state_store_protocol(self, tmp_path: Path) -> None:
        assert isinstance(_make_store(tmp_path), StateStoreLike)


class TestPostStorage:
    def test_add_post_round_trip(self, tmp_path: Path, make_post: Callable[..., Post]) -> None:
        store = _make_store(tmp_path)
        post = make_post(post_id="p-1")
        store.add_post(post)
        assert store.get_post("p-1") == post

    def test_add_post_duplicate_raises_value_error(
        self, tmp_path: Path, make_post: Callable[..., Post]
    ) -> None:
        store = _make_store(tmp_path)
        post = make_post(post_id="p-1")
        store.add_post(post)
        with pytest.raises(ValueError, match="already exists"):
            store.add_post(post)

    def test_replace_post_unknown_raises_key_error(
        self, tmp_path: Path, make_post: Callable[..., Post]
    ) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(KeyError):
            store.replace_post(make_post(post_id="ghost"))

    def test_get_post_unknown_raises_key_error(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(KeyError):
            store.get_post("ghost")

    def test_list_posts_sorted_by_post_id(
        self, tmp_path: Path, make_post: Callable[..., Post]
    ) -> None:
        store = _make_store(tmp_path)
        store.add_post(make_post(post_id="p-3"))
        store.add_post(make_post(post_id="p-1"))
        store.add_post(make_post(post_id="p-2"))
        assert tuple(p.post_id for p in store.list_posts()) == ("p-1", "p-2", "p-3")

    def test_list_agent_ids_sorted(self, tmp_path: Path, make_agent: Callable[..., Agent]) -> None:
        agents = [make_agent(agent_id=aid) for aid in ("zeta", "alpha", "mu")]
        store = _make_store(tmp_path, agents=agents)
        assert store.list_agent_ids() == ("alpha", "mu", "zeta")


class TestRandomSeed:
    def test_seed_is_deterministic_per_agent(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, global_seed=42)
        assert store.get_random_seed("a-001") == store.get_random_seed("a-001")

    def test_seed_varies_across_agents(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, global_seed=42)
        assert store.get_random_seed("a-001") != store.get_random_seed("a-002")

    def test_seed_varies_across_global_seeds(self, tmp_path: Path) -> None:
        a = _make_store(tmp_path / "a", global_seed=1)
        b = _make_store(tmp_path / "b", global_seed=2)
        assert a.get_random_seed("agent") != b.get_random_seed("agent")


class TestSerialization:
    def test_to_dict_deterministic_across_repeated_dumps(
        self,
        tmp_path: Path,
        make_agent: Callable[..., Agent],
        make_post: Callable[..., Post],
    ) -> None:
        agents = [make_agent(agent_id=f"a-{i}") for i in range(3)]
        social = FakeSocialGraph()
        social.follow("a-0", "a-1")
        social.follow("a-2", "a-1")
        store = _make_store(tmp_path, agents=agents, social=social)
        store.add_post(make_post(post_id="p-2"))
        store.add_post(make_post(post_id="p-1"))
        first = store._serialize_to_dict()
        second = store._serialize_to_dict()
        assert first == second
        assert list(first["agents"]) == sorted(first["agents"])
        assert list(first["posts"]) == sorted(first["posts"])
        assert list(first["social"]) == sorted(first["social"])

    def test_global_seed_recorded_in_payload(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path, global_seed=7)
        assert store._serialize_to_dict()["global_seed"] == 7


class TestCheckpoint:
    async def test_save_creates_file_with_zero_padded_name(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        path = await store.save_checkpoint(7)
        assert path.name == "checkpoint_round_0007.json"
        assert path.exists()

    async def test_save_negative_round_rejected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(ValueError, match="round_num"):
            await store.save_checkpoint(-1)

    async def test_save_is_idempotent(
        self,
        tmp_path: Path,
        make_post: Callable[..., Post],
    ) -> None:
        store = _make_store(tmp_path)
        store.add_post(make_post(post_id="p-1"))
        first = await store.save_checkpoint(5)
        first_bytes = first.read_bytes()
        second = await store.save_checkpoint(5)
        assert first == second
        assert second.read_bytes() == first_bytes

    async def test_restore_round_trips_state(
        self,
        tmp_path: Path,
        make_agent: Callable[..., Agent],
        make_post: Callable[..., Post],
    ) -> None:
        agents = [make_agent(agent_id=f"a-{i}") for i in range(2)]
        social = FakeSocialGraph()
        social.follow("a-0", "a-1")
        store = _make_store(tmp_path, agents=agents, social=social)
        store.add_post(make_post(post_id="p-1", topics=("ai",)))
        store.add_post(make_post(post_id="p-2", topics=("music",)))
        await store.save_checkpoint(3)

        fresh = _make_store(tmp_path)
        await fresh.restore_checkpoint(3)

        assert fresh.list_agent_ids() == ("a-0", "a-1")
        assert tuple(p.post_id for p in fresh.list_posts()) == ("p-1", "p-2")
        assert fresh.social.following_count("a-0") == 1
        assert fresh.social.followers("a-1") == frozenset({"a-0"})

    async def test_restore_missing_round_raises(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(FileNotFoundError):
            await store.restore_checkpoint(99)

    async def test_global_seed_mismatch_on_restore_raises(self, tmp_path: Path) -> None:
        a = _make_store(tmp_path, global_seed=1)
        await a.save_checkpoint(0)
        b = _make_store(tmp_path / "other", global_seed=2)
        copied = b.checkpoint_dir / "checkpoint_round_0000.json"
        copied.write_text(
            (a.checkpoint_dir / "checkpoint_round_0000.json").read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="global_seed mismatch"):
            await b.restore_checkpoint(0)


class TestRestoreAtomicity:
    """Restore must be all-or-nothing: any failure leaves the store unchanged."""

    @staticmethod
    def _snapshot(
        store: StateStore,
    ) -> tuple[tuple[str, ...], tuple[str, ...], dict[str, list[str]]]:
        return (
            store.list_agent_ids(),
            tuple(p.post_id for p in store.list_posts()),
            dict(store.social.to_dict()),
        )

    def _populated_store(
        self,
        tmp_path: Path,
        make_agent: Callable[..., Agent],
        make_post: Callable[..., Post],
        *,
        global_seed: int = 1,
    ) -> StateStore:
        social = FakeSocialGraph()
        social.follow("orig-a", "orig-b")
        store = _make_store(
            tmp_path,
            agents=[make_agent(agent_id="orig-a"), make_agent(agent_id="orig-b")],
            social=social,
            global_seed=global_seed,
        )
        store.add_post(make_post(post_id="orig-p"))
        return store

    def test_seed_mismatch_leaves_state_unchanged(
        self,
        tmp_path: Path,
        make_agent: Callable[..., Agent],
        make_post: Callable[..., Post],
    ) -> None:
        store = self._populated_store(tmp_path, make_agent, make_post, global_seed=1)
        before = self._snapshot(store)
        payload = {
            "agents": {"new-a": make_agent(agent_id="new-a").model_dump(mode="json")},
            "posts": {"new-p": make_post(post_id="new-p").model_dump(mode="json")},
            "social": {"new-a": ["new-b"]},
            "global_seed": 999,
        }
        with pytest.raises(ValueError, match="global_seed mismatch"):
            store._deserialize_from_dict(payload)
        assert self._snapshot(store) == before

    def test_missing_agents_key_raises_and_leaves_unchanged(
        self,
        tmp_path: Path,
        make_agent: Callable[..., Agent],
        make_post: Callable[..., Post],
    ) -> None:
        store = self._populated_store(tmp_path, make_agent, make_post, global_seed=1)
        before = self._snapshot(store)
        with pytest.raises(ValueError, match="malformed checkpoint"):
            store._deserialize_from_dict({"posts": {}, "global_seed": 1})
        assert self._snapshot(store) == before

    def test_missing_posts_key_raises_and_leaves_unchanged(
        self,
        tmp_path: Path,
        make_agent: Callable[..., Agent],
        make_post: Callable[..., Post],
    ) -> None:
        store = self._populated_store(tmp_path, make_agent, make_post, global_seed=1)
        before = self._snapshot(store)
        with pytest.raises(ValueError, match="malformed checkpoint"):
            store._deserialize_from_dict({"agents": {}, "global_seed": 1})
        assert self._snapshot(store) == before

    def test_invalid_agent_data_raises_and_leaves_unchanged(
        self,
        tmp_path: Path,
        make_agent: Callable[..., Agent],
        make_post: Callable[..., Post],
    ) -> None:
        store = self._populated_store(tmp_path, make_agent, make_post, global_seed=1)
        before = self._snapshot(store)
        payload = {
            "agents": {"bad": {"agent_id": 42}},  # agent_id must be str
            "posts": {},
            "social": {},
            "global_seed": 1,
        }
        with pytest.raises(ValidationError):
            store._deserialize_from_dict(payload)
        assert self._snapshot(store) == before

    def test_invalid_post_data_raises_and_leaves_unchanged(
        self,
        tmp_path: Path,
        make_agent: Callable[..., Agent],
        make_post: Callable[..., Post],
    ) -> None:
        # Agent block parses cleanly, but post block fails — proves the
        # swap happens after BOTH parse, not field-by-field.
        store = self._populated_store(tmp_path, make_agent, make_post, global_seed=1)
        before = self._snapshot(store)
        payload = {
            "agents": {"new-a": make_agent(agent_id="new-a").model_dump(mode="json")},
            "posts": {"bad": {"post_id": "bad"}},  # missing required fields
            "social": {},
            "global_seed": 1,
        }
        with pytest.raises(ValidationError):
            store._deserialize_from_dict(payload)
        assert self._snapshot(store) == before

    def test_missing_global_seed_key_raises_and_leaves_unchanged(
        self,
        tmp_path: Path,
        make_agent: Callable[..., Agent],
        make_post: Callable[..., Post],
    ) -> None:
        # _serialize_to_dict always writes global_seed; refuse hand-crafted
        # checkpoints that omit it so a wrong-seeded store can't silently
        # accept them.
        store = self._populated_store(tmp_path, make_agent, make_post, global_seed=1)
        before = self._snapshot(store)
        with pytest.raises(ValueError, match="global_seed"):
            store._deserialize_from_dict({"agents": {}, "posts": {}})
        assert self._snapshot(store) == before

    def test_social_factory_raise_leaves_state_unchanged(
        self,
        tmp_path: Path,
        make_agent: Callable[..., Agent],
        make_post: Callable[..., Post],
    ) -> None:
        # Self-follow trips FakeSocialGraph.from_dict — agents/posts blocks
        # parse cleanly, so this pins that the swap waits for social too.
        store = self._populated_store(tmp_path, make_agent, make_post, global_seed=1)
        before = self._snapshot(store)
        payload = {
            "agents": {"new-a": make_agent(agent_id="new-a").model_dump(mode="json")},
            "posts": {"new-p": make_post(post_id="new-p").model_dump(mode="json")},
            "social": {"x": ["x"]},
            "global_seed": 1,
        }
        with pytest.raises(ValueError, match="self-follow"):
            store._deserialize_from_dict(payload)
        assert self._snapshot(store) == before


class TestPruneAndLatest:
    async def test_keep_three_most_recent(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        for n in range(5):
            await store.save_checkpoint(n)
        names = sorted(p.name for p in store.checkpoint_dir.iterdir())
        assert names == [
            "checkpoint_round_0002.json",
            "checkpoint_round_0003.json",
            "checkpoint_round_0004.json",
        ]

    async def test_latest_checkpoint_round(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        assert store.latest_checkpoint_round() is None
        await store.save_checkpoint(0)
        await store.save_checkpoint(2)
        await store.save_checkpoint(1)
        assert store.latest_checkpoint_round() == 2

    def test_prune_keep_zero_rejected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        with pytest.raises(ValueError, match="keep"):
            store._prune_old_checkpoints(keep=0)


class TestSocialGraphSerialization:
    async def test_to_dict_from_dict_round_trip(
        self,
        tmp_path: Path,
    ) -> None:
        social = FakeSocialGraph()
        social.follow("a", "b")
        social.follow("a", "c")
        social.follow("b", "c")
        store = _make_store(tmp_path, social=social)
        await store.save_checkpoint(0)

        fresh = _make_store(tmp_path)
        assert fresh.social.to_dict() == {}
        await fresh.restore_checkpoint(0)
        assert fresh.social.to_dict() == {"a": ["b", "c"], "b": ["c"]}

    async def test_social_factory_is_called_with_dict(
        self,
        tmp_path: Path,
    ) -> None:
        captured: list[Mapping[str, Iterable[str]]] = []

        def tracking_factory(
            data: Mapping[str, Iterable[str]],
        ) -> SocialGraphLike:
            captured.append(data)
            return FakeSocialGraph.from_dict(data)

        social = FakeSocialGraph()
        social.follow("a", "b")
        store = StateStore(
            agents=(),
            social=social,
            social_factory=tracking_factory,
            checkpoint_dir=tmp_path / "checkpoints",
            global_seed=0,
        )
        await store.save_checkpoint(0)
        await store.restore_checkpoint(0)
        assert captured == [{"a": ["b"]}]
