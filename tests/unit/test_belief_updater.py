"""``BeliefUpdater`` 단위 테스트."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from litemiro.core.belief_updater import BeliefUpdater
from litemiro.models import Action, ActionType, Agent, LLMMeta, Post, RoundEvent
from tests.fakes import InMemoryStateStore

_META = LLMMeta(model="fake", tokens_used=0, latency_ms=0.0)


def _agent(agent_id: str, ideology: float) -> Agent:
    return Agent(agent_id=agent_id, ideology=ideology)


def _post(post_id: str, author_id: str) -> Post:
    return Post(
        post_id=post_id,
        author_id=author_id,
        content="test",
        created_round=0,
    )


def _event(agent_id: str, action: Action, round_num: int = 0) -> RoundEvent:
    return RoundEvent(
        round_num=round_num,
        timestamp=datetime.now(UTC),
        agent_id=agent_id,
        action=action,
        llm_meta=_META,
    )


def _make_store(*agents: Agent, posts: list[Post] | None = None) -> InMemoryStateStore:
    store = InMemoryStateStore(agents={a.agent_id: a for a in agents})
    for p in posts or []:
        store.add_post(p)
    return store


class TestFollowUpdate:
    def test_follow_within_epsilon_shifts_ideology(self) -> None:
        a = _agent("a", 0.3)
        b = _agent("b", 0.5)
        store = _make_store(a, b)
        updater = BeliefUpdater(epsilon=0.3, mu_follow=0.1)

        events = [_event("a", Action(type=ActionType.FOLLOW, target_agent_id="b"))]
        pending = updater.apply_round(events, store, round_num=0)

        assert "a" in pending
        new_ideo = store.get_agent("a").ideology
        assert new_ideo > 0.3  # moved toward b (0.5)
        assert abs(new_ideo - (0.3 + 0.1 * (0.5 - 0.3))) < 1e-9

    def test_follow_outside_epsilon_no_change(self) -> None:
        a = _agent("a", 0.1)
        b = _agent("b", 0.9)
        store = _make_store(a, b)
        updater = BeliefUpdater(epsilon=0.3, mu_follow=0.1)

        events = [_event("a", Action(type=ActionType.FOLLOW, target_agent_id="b"))]
        pending = updater.apply_round(events, store, round_num=0)

        assert "a" not in pending
        assert store.get_agent("a").ideology == 0.1

    def test_do_nothing_no_change(self) -> None:
        a = _agent("a", 0.5)
        store = _make_store(a)
        updater = BeliefUpdater()

        events = [_event("a", Action(type=ActionType.DO_NOTHING))]
        pending = updater.apply_round(events, store, round_num=0)

        assert pending == {}
        assert store.get_agent("a").ideology == 0.5


class TestLikeUpdate:
    def test_like_shifts_less_than_follow(self) -> None:
        a = _agent("a", 0.3)
        b = _agent("b", 0.5)
        post = _post("p1", "b")
        store = _make_store(a, b, posts=[post])
        updater = BeliefUpdater(epsilon=0.3, mu_follow=0.3)

        follow_events = [_event("a", Action(type=ActionType.FOLLOW, target_agent_id="b"))]
        like_events = [_event("a", Action(type=ActionType.LIKE_POST, target_post_id="p1"))]

        store_follow = _make_store(a, b, posts=[post])
        store_like = _make_store(a, b, posts=[post])

        BeliefUpdater(epsilon=0.3, mu_follow=0.3).apply_round(follow_events, store_follow, 0)
        BeliefUpdater(epsilon=0.3, mu_follow=0.3).apply_round(like_events, store_like, 0)

        follow_shift = store_follow.get_agent("a").ideology - 0.3
        like_shift = store_like.get_agent("a").ideology - 0.3
        assert like_shift < follow_shift
        assert like_shift > 0

    def test_like_unknown_post_ignored(self) -> None:
        a = _agent("a", 0.5)
        store = _make_store(a)
        updater = BeliefUpdater()

        events = [_event("a", Action(type=ActionType.LIKE_POST, target_post_id="missing"))]
        pending = updater.apply_round(events, store, round_num=0)

        assert pending == {}


class TestBoundedClamp:
    def test_ideology_clamped_at_1(self) -> None:
        a = _agent("a", 0.98)
        b = _agent("b", 1.0)
        store = _make_store(a, b)
        updater = BeliefUpdater(epsilon=0.3, mu_follow=1.0)

        events = [_event("a", Action(type=ActionType.FOLLOW, target_agent_id="b"))]
        updater.apply_round(events, store, round_num=0)

        assert store.get_agent("a").ideology <= 1.0

    def test_ideology_clamped_at_0(self) -> None:
        a = _agent("a", 0.02)
        b = _agent("b", 0.0)
        store = _make_store(a, b)
        updater = BeliefUpdater(epsilon=0.3, mu_follow=1.0)

        events = [_event("a", Action(type=ActionType.FOLLOW, target_agent_id="b"))]
        updater.apply_round(events, store, round_num=0)

        assert store.get_agent("a").ideology >= 0.0


class TestBatchApply:
    def test_batch_uses_pre_round_ideology(self) -> None:
        # a follows b, then a follows c — 두 번째 follow 는 첫 번째 결과가
        # 아닌 라운드 시작 ideology 를 기준으로 해야 한다.
        a = _agent("a", 0.4)
        b = _agent("b", 0.5)
        c = _agent("c", 0.5)
        store = _make_store(a, b, c)
        updater = BeliefUpdater(epsilon=0.3, mu_follow=0.1)

        events = [
            _event("a", Action(type=ActionType.FOLLOW, target_agent_id="b")),
            _event("a", Action(type=ActionType.FOLLOW, target_agent_id="c")),
        ]
        updater.apply_round(events, store, round_num=0)

        # 배치 적용: 두 event 모두 pre_ideology=0.4 기준
        # shift = 0.1 * (0.5 - 0.4) = 0.01, 두 번 누적 → 0.4 + 0.02
        expected = 0.4 + 0.1 * (0.5 - 0.4) + 0.1 * (0.5 - 0.4)
        assert abs(store.get_agent("a").ideology - expected) < 1e-9


class TestTrajectory:
    def test_trajectory_file_written(self, tmp_path: Path) -> None:
        a = _agent("a", 0.3)
        b = _agent("b", 0.7)
        store = _make_store(a, b)
        traj_path = tmp_path / "belief_trajectory.jsonl"
        updater = BeliefUpdater(trajectory_path=traj_path)

        events = [_event("a", Action(type=ActionType.FOLLOW, target_agent_id="b"))]
        updater.apply_round(events, store, round_num=5)
        updater.close()

        lines = traj_path.read_text().strip().splitlines()
        assert len(lines) == 1
        row = json.loads(lines[0])
        assert row["round_num"] == 5
        assert "a" in row["ideology"]
        assert "b" in row["ideology"]

    def test_no_trajectory_without_path(self) -> None:
        a = _agent("a", 0.5)
        store = _make_store(a)
        updater = BeliefUpdater()  # no trajectory_path

        events = [_event("a", Action(type=ActionType.DO_NOTHING))]
        updater.apply_round(events, store, round_num=0)
        updater.close()  # no error
