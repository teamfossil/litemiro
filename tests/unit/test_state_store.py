from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

import pytest

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
