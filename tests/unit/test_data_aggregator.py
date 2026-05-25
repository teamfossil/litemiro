"""DataAggregator 단위 테스트.

JSONL 라인을 결정적 카테고리 dict 로 줄이는 단일 책임을 검증한다.
모든 출력은 동일 입력에 대해 같아야 한다 (재현성 강제).
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from litemiro.models import Action, ActionType, RoundEvent
from litemiro.phase3 import AggregationResult, DataAggregator
from litemiro.phase3.models import (
    CATEGORY_ACTION_DISTRIBUTION,
    CATEGORY_NETWORK_METRICS,
    CATEGORY_TIME_SERIES,
    CATEGORY_TOPIC_FLOW,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_JSONL = _REPO_ROOT / "tests" / "data" / "round_event_sample.jsonl"


def _event(
    *,
    round_num: int,
    agent_id: str,
    action_type: ActionType,
    target_post_id: str | None = None,
    target_agent_id: str | None = None,
    content: str | None = None,
    offset_seconds: int = 0,
) -> RoundEvent:
    return RoundEvent(
        round_num=round_num,
        timestamp=datetime(2026, 4, 1, 10, 0, offset_seconds, tzinfo=UTC),
        agent_id=agent_id,
        action=Action(
            type=action_type,
            target_post_id=target_post_id,
            target_agent_id=target_agent_id,
            content=content,
        ),
    )


class TestAggregateEvents:
    def test_empty_input_yields_zero_counts(self) -> None:
        result = DataAggregator.aggregate_events([])
        assert result.n_events == 0
        assert result.n_agents == 0
        assert result.n_rounds == 0
        action = result.categories[CATEGORY_ACTION_DISTRIBUTION]
        assert action["total"] == 1  # zero-guarded denominator
        assert sum(action["counts"].values()) == 0

    def test_action_distribution_counts_and_ratios(self) -> None:
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.CREATE_POST, content="x"),
            _event(round_num=0, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="b", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="b", action_type=ActionType.DO_NOTHING),
        ]
        result = DataAggregator.aggregate_events(events)
        action = result.categories[CATEGORY_ACTION_DISTRIBUTION]
        assert action["counts"]["LIKE_POST"] == 2
        assert action["counts"]["CREATE_POST"] == 1
        assert action["counts"]["DO_NOTHING"] == 1
        assert action["counts"]["FOLLOW"] == 0
        assert action["total"] == 4
        assert action["ratios"]["LIKE_POST"] == pytest.approx(0.5)
        # ActionType enum 순서 유지
        assert list(action["counts"].keys()) == [t.value for t in ActionType]

    def test_top_active_agents_ordered_by_count_then_id(self) -> None:
        events = [
            _event(round_num=0, agent_id="z", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="a", action_type=ActionType.DO_NOTHING),
            _event(round_num=0, agent_id="b", action_type=ActionType.LIKE_POST, target_post_id="p"),
        ]
        result = DataAggregator.aggregate_events(events)
        top = result.categories[CATEGORY_ACTION_DISTRIBUTION]["top_active_agents"]
        # a: 2, b: 1, z: 1 — count desc, then id asc
        assert [row["agent_id"] for row in top] == ["a", "b", "z"]
        assert top[0]["actions"] == 2

    def test_network_metrics_counts_follow_only(self) -> None:
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="b"),
            _event(round_num=1, agent_id="c", action_type=ActionType.FOLLOW, target_agent_id="b"),
            _event(round_num=1, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="d"),
            _event(round_num=2, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
        ]
        result = DataAggregator.aggregate_events(events)
        net = result.categories[CATEGORY_NETWORK_METRICS]
        assert net["n_follow_events"] == 3
        followed = {row["agent_id"]: row["follows_received"] for row in net["top_followed"]}
        assert followed["b"] == 2
        assert followed["d"] == 1
        followers = {row["agent_id"]: row["follows_given"] for row in net["top_followers"]}
        assert followers["a"] == 2
        assert followers["c"] == 1

    def test_topic_flow_collects_posts_and_quote_posts(self) -> None:
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.CREATE_POST, content="hello"),
            _event(
                round_num=1,
                agent_id="b",
                action_type=ActionType.QUOTE_POST,
                target_post_id="p",
                content="agreed",
            ),
            _event(round_num=1, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
        ]
        result = DataAggregator.aggregate_events(events)
        topic = result.categories[CATEGORY_TOPIC_FLOW]
        assert topic["n_posts"] == 2
        assert {row["round_num"]: row["n"] for row in topic["posts_per_round"]} == {0: 1, 1: 1}
        assert len(topic["samples"]) == 2
        contents = [s["content"] for s in topic["samples"]]
        assert "hello" in contents
        assert "agreed" in contents

    def test_topic_flow_caps_samples_at_limit(self) -> None:
        events = [
            _event(
                round_num=r,
                agent_id=f"a-{r:02d}",
                action_type=ActionType.CREATE_POST,
                content=f"c{r}",
            )
            for r in range(15)
        ]
        result = DataAggregator.aggregate_events(events)
        topic = result.categories[CATEGORY_TOPIC_FLOW]
        assert topic["n_posts"] == 15
        assert len(topic["samples"]) == 10  # _TOPIC_FLOW_SAMPLE_LIMIT

    def test_time_series_per_round_metrics(self) -> None:
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.CREATE_POST, content="x"),
            _event(round_num=0, agent_id="b", action_type=ActionType.DO_NOTHING),
            _event(round_num=1, agent_id="a", action_type=ActionType.DO_NOTHING),
            _event(round_num=1, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
        ]
        result = DataAggregator.aggregate_events(events)
        ts = result.categories[CATEGORY_TIME_SERIES]
        assert ts["rounds"] == [0, 1]
        r0, r1 = ts["series"]
        assert r0["round_num"] == 0
        assert r0["n_actions"] == 2
        assert r0["n_do_nothing"] == 1
        assert r0["do_nothing_ratio"] == pytest.approx(0.5)
        assert r0["n_active_agents"] == 2
        assert r1["n_active_agents"] == 1  # a 만 active

    def test_aggregation_is_deterministic_for_same_input(self) -> None:
        events = [
            _event(round_num=0, agent_id="b", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
        ]
        first = DataAggregator.aggregate_events(events).model_dump()
        second = DataAggregator.aggregate_events(events).model_dump()
        assert first == second


class TestAggregateFromJsonl:
    def test_round_trip_from_sample_fixture(self) -> None:
        result = DataAggregator.aggregate(_SAMPLE_JSONL)
        assert isinstance(result, AggregationResult)
        # 6 lines / 4 agents (a-001..a-004) / 4 rounds (0..3)
        assert result.n_events == 6
        assert result.n_agents == 4
        assert result.n_rounds == 4

        action = result.categories[CATEGORY_ACTION_DISTRIBUTION]
        assert action["counts"]["CREATE_POST"] == 1
        assert action["counts"]["FOLLOW"] == 1
        assert action["counts"]["DO_NOTHING"] == 1

        net = result.categories[CATEGORY_NETWORK_METRICS]
        assert net["n_follow_events"] == 1
        assert net["top_followed"][0] == {"agent_id": "a-003", "follows_received": 1}

        topic = result.categories[CATEGORY_TOPIC_FLOW]
        assert topic["n_posts"] == 2  # CREATE_POST + QUOTE_POST

    def test_blank_lines_are_skipped(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text(
            '{"round_num":0,"timestamp":"2026-04-01T10:00:00+00:00","agent_id":"a",'
            '"action":{"type":"DO_NOTHING"}}\n\n\n',
            encoding="utf-8",
        )
        result = DataAggregator.aggregate(path)
        assert result.n_events == 1

    def test_malformed_json_raises_with_line_number(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        path.write_text("not json\n", encoding="utf-8")
        with pytest.raises(ValueError, match=r":1 JSON 파싱 실패"):
            DataAggregator.aggregate(path)

    def test_validation_failure_raises_with_line_number(self, tmp_path: Path) -> None:
        path = tmp_path / "events.jsonl"
        # round_num must be >= 0
        path.write_text(
            '{"round_num":-1,"timestamp":"2026-04-01T10:00:00+00:00","agent_id":"a",'
            '"action":{"type":"DO_NOTHING"}}\n',
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match=r":1 RoundEvent 검증 실패"):
            DataAggregator.aggregate(path)
