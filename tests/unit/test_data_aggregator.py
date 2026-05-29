"""DataAggregator 단위 테스트.

JSONL 라인을 결정적 카테고리 dict 로 줄이는 단일 책임을 검증한다.
모든 출력은 동일 입력에 대해 같아야 한다 (재현성 강제).
"""

from __future__ import annotations

import math
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

    def test_topic_flow_separates_content_posts_from_repost_amplifications(self) -> None:
        """REPOST 도 round_manager 가 새 Post 를 만들어 feed 에 띄우니까 "신규 포스트"
        총합엔 들어가야 하지만 (#110), 본문이 있는 게시물 카운트 / 작성자 인사이트는
        CREATE/QUOTE 만 봐야 한다. 두 의미를 분리한 키가 모두 노출되는지 확인."""
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.CREATE_POST, content="hi"),
            _event(
                round_num=1,
                agent_id="b",
                action_type=ActionType.QUOTE_POST,
                target_post_id="p",
                content="add",
            ),
            _event(round_num=1, agent_id="c", action_type=ActionType.REPOST, target_post_id="p"),
            _event(round_num=1, agent_id="d", action_type=ActionType.REPOST, target_post_id="p"),
            _event(round_num=1, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
        ]
        result = DataAggregator.aggregate_events(events)
        topic = result.categories[CATEGORY_TOPIC_FLOW]
        assert topic["n_content_posts"] == 2
        assert topic["n_posts"] == 2  # 호환 alias
        assert topic["n_amplifications"] == 2
        assert topic["total_posts_created"] == 4
        # 작성자는 본문 있는 게시물 기준 — REPOST 한 c/d 는 작성자 카운트에 들어가지 않음.
        posters = {row["agent_id"]: row["posts"] for row in topic["top_posters"]}
        assert posters == {"a": 1, "b": 1}

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

    def test_time_series_aggregate_exposes_zero_do_nothing_round_count(self) -> None:
        """analyzer LLM 이 series 를 직접 세서 "14/15 라운드 0%" 같은 환각을 내지
        않도록 사전 합산값을 노출 (#110). round 0: DO_NOTHING 1/2, round 1: 0/1,
        round 2: 1/1 → zero 인 라운드는 1 (round 1)."""
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.CREATE_POST, content="x"),
            _event(round_num=0, agent_id="b", action_type=ActionType.DO_NOTHING),
            _event(round_num=1, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=2, agent_id="a", action_type=ActionType.DO_NOTHING),
        ]
        result = DataAggregator.aggregate_events(events)
        agg = result.categories[CATEGORY_TIME_SERIES]["aggregate"]
        assert agg["n_rounds"] == 3
        assert agg["n_rounds_with_do_nothing"] == 2
        assert agg["n_rounds_zero_do_nothing"] == 1
        assert agg["avg_do_nothing_ratio"] == pytest.approx((0.5 + 0.0 + 1.0) / 3)
        assert agg["max_do_nothing_ratio"] == pytest.approx(1.0)

    def test_topic_flow_samples_round_robin_across_rounds(self) -> None:
        # 라운드 0 에 8 개, 라운드 1·2 에 1 개씩 (총 10 = limit). 이벤트 순서대로
        # 앞 10 개를 자르면 라운드 0 이 표본을 독점하지만, round-robin 은 후반
        # 라운드도 표본에 넣는다.
        events = [
            _event(
                round_num=0, agent_id=f"a{i}", action_type=ActionType.CREATE_POST, content=f"r0-{i}"
            )
            for i in range(8)
        ]
        events.append(
            _event(round_num=1, agent_id="b", action_type=ActionType.CREATE_POST, content="r1")
        )
        events.append(
            _event(round_num=2, agent_id="c", action_type=ActionType.CREATE_POST, content="r2")
        )
        topic = DataAggregator.aggregate_events(events).categories[CATEGORY_TOPIC_FLOW]
        assert len(topic["samples"]) == 10
        assert {s["round_num"] for s in topic["samples"]} == {0, 1, 2}

    def test_topic_flow_samples_not_dominated_by_first_round(self) -> None:
        # 라운드 0 에 15 개, 라운드 1 에 5 개. 순서 절단이면 표본 10 개가 전부
        # 라운드 0 이지만, round-robin 은 라운드 1 의 5 개를 모두 표본에 넣고
        # 라운드 순으로 정렬해 돌려준다.
        events = [
            _event(
                round_num=0, agent_id=f"a{i}", action_type=ActionType.CREATE_POST, content=f"r0-{i}"
            )
            for i in range(15)
        ]
        events += [
            _event(
                round_num=1, agent_id=f"b{i}", action_type=ActionType.CREATE_POST, content=f"r1-{i}"
            )
            for i in range(5)
        ]
        samples = DataAggregator.aggregate_events(events).categories[CATEGORY_TOPIC_FLOW]["samples"]
        rounds = [s["round_num"] for s in samples]
        assert len(samples) == 10
        assert rounds.count(1) == 5  # 후반 라운드가 사라지지 않음
        assert rounds == sorted(rounds)  # 라운드 순 정렬

    def test_distribution_concentration_for_skewed_activity(self) -> None:
        # a 3, b 1, c 1 → 고유 3, 상위5 = 전부(0.5 미만 아님), gini 양수.
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="a", action_type=ActionType.DO_NOTHING),
            _event(round_num=0, agent_id="b", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="c", action_type=ActionType.DO_NOTHING),
        ]
        conc = DataAggregator.aggregate_events(events).categories[CATEGORY_ACTION_DISTRIBUTION][
            "agent_activity_concentration"
        ]
        assert conc["n_unique"] == 3
        assert conc["top5_share"] == pytest.approx(1.0)
        assert 0.0 < conc["gini"] < 1.0

    def test_gini_zero_for_uniform_distribution(self) -> None:
        events = [
            _event(round_num=0, agent_id=a, action_type=ActionType.DO_NOTHING)
            for a in ("a", "b", "c", "d")
        ]
        conc = DataAggregator.aggregate_events(events).categories[CATEGORY_ACTION_DISTRIBUTION][
            "agent_activity_concentration"
        ]
        assert conc["n_unique"] == 4
        assert conc["gini"] == pytest.approx(0.0)

    def test_top5_share_below_one_with_long_tail(self) -> None:
        # 10 명이 각 1 행동 → 상위 5 점유율 0.5, gini 0. top_* 리스트(상위 10)
        # 너머 분포가 없으니 롱테일은 균등.
        events = [
            _event(round_num=0, agent_id=f"a{i:02d}", action_type=ActionType.DO_NOTHING)
            for i in range(10)
        ]
        conc = DataAggregator.aggregate_events(events).categories[CATEGORY_ACTION_DISTRIBUTION][
            "agent_activity_concentration"
        ]
        assert conc["n_unique"] == 10
        assert conc["top5_share"] == pytest.approx(0.5)
        assert conc["gini"] == pytest.approx(0.0)

    def test_network_and_poster_concentration_exposed(self) -> None:
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="hub"),
            _event(round_num=0, agent_id="b", action_type=ActionType.FOLLOW, target_agent_id="hub"),
            _event(round_num=0, agent_id="c", action_type=ActionType.CREATE_POST, content="x"),
            _event(round_num=0, agent_id="c", action_type=ActionType.CREATE_POST, content="y"),
        ]
        cats = DataAggregator.aggregate_events(events).categories
        net = cats[CATEGORY_NETWORK_METRICS]
        assert net["followee_concentration"]["n_unique"] == 1  # hub 한 명만 수신
        assert net["follower_concentration"]["n_unique"] == 2  # a, b 발신
        topic = cats[CATEGORY_TOPIC_FLOW]
        assert topic["poster_concentration"]["n_unique"] == 1  # c 만 작성

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

    def test_malformed_json_line_is_skipped(self, tmp_path: Path) -> None:
        # 한 줄만 깨졌다고 보고서 전체가 죽으면 안 된다. ``api/store.py``
        # ``_parse_event_log`` 와 동일 lenient 패턴 — 같은 jsonl 이 SSE 재연결엔
        # 살아있고 ``/report`` 엔 죽는 비대칭을 막는다.
        path = tmp_path / "events.jsonl"
        path.write_text(
            "not json\n"
            '{"round_num":0,"timestamp":"2026-04-01T10:00:00+00:00","agent_id":"a",'
            '"action":{"type":"DO_NOTHING"}}\n',
            encoding="utf-8",
        )
        result = DataAggregator.aggregate(path)
        assert result.n_events == 1

    def test_validation_failure_line_is_skipped(self, tmp_path: Path) -> None:
        # round_num < 0 한 줄을 끼워도 나머지는 집계된다.
        path = tmp_path / "events.jsonl"
        path.write_text(
            '{"round_num":-1,"timestamp":"2026-04-01T10:00:00+00:00","agent_id":"a",'
            '"action":{"type":"DO_NOTHING"}}\n'
            '{"round_num":0,"timestamp":"2026-04-01T10:00:00+00:00","agent_id":"a",'
            '"action":{"type":"DO_NOTHING"}}\n',
            encoding="utf-8",
        )
        result = DataAggregator.aggregate(path)
        assert result.n_events == 1
        assert result.n_rounds == 1

    def test_all_lines_corrupt_returns_empty(self, tmp_path: Path) -> None:
        # 전부 깨졌으면 빈 집계 — DataAggregator 자체는 빈 events 도 정상 처리.
        path = tmp_path / "events.jsonl"
        path.write_text('not json\n{"x":1}\nbroken\n', encoding="utf-8")
        result = DataAggregator.aggregate(path)
        assert result.n_events == 0
        assert result.n_agents == 0
        assert result.n_rounds == 0


class TestQaMetrics:
    """OASIS 등가성 게이트용 결정적 수치 검증 (`docs/qa/metrics.md`).

    범위 [0, 1] 안에 정규화되어야 하고, 동일 입력에 같은 값을 돌려준다.
    """

    def test_empty_events_all_zero(self) -> None:
        qa = DataAggregator.aggregate_events([]).qa_metrics
        assert qa.action_entropy_normalized == 0.0
        assert qa.follow_clustering_coefficient == 0.0
        assert qa.content_word_entropy_normalized == 0.0

    def test_action_entropy_zero_when_single_type(self) -> None:
        events = [
            _event(round_num=0, agent_id=f"a-{i}", action_type=ActionType.DO_NOTHING)
            for i in range(6)
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        assert qa.action_entropy_normalized == 0.0

    def test_action_entropy_one_when_uniform_over_all_types(self) -> None:
        # ActionType 6 종을 똑같이 1 회씩 → Shannon = log2(6), 정규화 후 1.0
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.CREATE_POST, content="x"),
            _event(round_num=0, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="a", action_type=ActionType.REPOST, target_post_id="p"),
            _event(
                round_num=0,
                agent_id="a",
                action_type=ActionType.QUOTE_POST,
                target_post_id="p",
                content="q",
            ),
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="b"),
            _event(round_num=0, agent_id="a", action_type=ActionType.DO_NOTHING),
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        assert qa.action_entropy_normalized == pytest.approx(1.0)

    def test_action_entropy_in_unit_interval_for_skewed(self) -> None:
        # 한 타입에 몰린 분포는 0 < H < 1
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="b", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="c", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="d", action_type=ActionType.CREATE_POST, content="x"),
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        assert 0.0 < qa.action_entropy_normalized < 1.0

    def test_clustering_zero_when_fewer_than_three_nodes(self) -> None:
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="b"),
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        assert qa.follow_clustering_coefficient == 0.0

    def test_clustering_one_for_triangle(self) -> None:
        # 삼각형: a-b, b-c, a-c 모두 양방향 FOLLOW (무방향 그래프에서 자동으로 채워짐)
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="b"),
            _event(round_num=0, agent_id="b", action_type=ActionType.FOLLOW, target_agent_id="c"),
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="c"),
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        assert qa.follow_clustering_coefficient == pytest.approx(1.0)

    def test_clustering_zero_for_star(self) -> None:
        # 별 그래프 hub a → b, c, d. b/c/d 간 엣지 없음 → 클러스터링 0.
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="b"),
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="c"),
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="d"),
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        assert qa.follow_clustering_coefficient == 0.0

    def test_clustering_ignores_self_loops_and_duplicates(self) -> None:
        # self-loop 무시 + 같은 엣지 중복 무시 → 결과는 단순 a-b 엣지 (노드 2 → 0.0)
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="a"),
            _event(round_num=0, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="b"),
            _event(round_num=1, agent_id="a", action_type=ActionType.FOLLOW, target_agent_id="b"),
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        assert qa.follow_clustering_coefficient == 0.0

    def test_content_entropy_zero_when_no_posts(self) -> None:
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.LIKE_POST, target_post_id="p"),
            _event(round_num=0, agent_id="b", action_type=ActionType.DO_NOTHING),
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        assert qa.content_word_entropy_normalized == 0.0

    def test_content_entropy_zero_for_single_repeated_word(self) -> None:
        # vocab = 1 → 정규화 분모 log2(1) = 0, 정의에 의해 0.0
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.CREATE_POST, content="hi"),
            _event(round_num=0, agent_id="b", action_type=ActionType.CREATE_POST, content="hi"),
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        assert qa.content_word_entropy_normalized == 0.0

    def test_content_entropy_one_for_uniform_vocab(self) -> None:
        # 세 단어가 각 1 회씩 → H = log2(3), vocab = 3 → 정규화 1.0
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.CREATE_POST, content="a b c"),
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        assert qa.content_word_entropy_normalized == pytest.approx(1.0)

    def test_content_entropy_includes_quote_posts(self) -> None:
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.CREATE_POST, content="x y"),
            _event(
                round_num=1,
                agent_id="b",
                action_type=ActionType.QUOTE_POST,
                target_post_id="p",
                content="z w",
            ),
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        # 단어 4 종 uniform → 1.0 (부동소수 오차 허용)
        assert qa.content_word_entropy_normalized == pytest.approx(1.0)

    def test_qa_metrics_within_unit_interval(self) -> None:
        # 부동소수 오차로 [0, 1] 밖으로 새지 않는다.
        events = [
            _event(
                round_num=r, agent_id=f"a-{r}", action_type=ActionType.CREATE_POST, content=f"w{r}"
            )
            for r in range(20)
        ]
        qa = DataAggregator.aggregate_events(events).qa_metrics
        for name in (
            "action_entropy_normalized",
            "follow_clustering_coefficient",
            "content_word_entropy_normalized",
        ):
            value = getattr(qa, name)
            assert 0.0 <= value <= 1.0, f"{name}={value} 가 단위 구간 밖"
            assert not math.isnan(value)

    def test_qa_metrics_deterministic(self) -> None:
        events = [
            _event(round_num=0, agent_id="a", action_type=ActionType.CREATE_POST, content="x y"),
            _event(round_num=0, agent_id="b", action_type=ActionType.FOLLOW, target_agent_id="a"),
            _event(round_num=1, agent_id="c", action_type=ActionType.FOLLOW, target_agent_id="a"),
            _event(round_num=1, agent_id="b", action_type=ActionType.FOLLOW, target_agent_id="c"),
        ]
        first = DataAggregator.aggregate_events(events).qa_metrics.model_dump()
        second = DataAggregator.aggregate_events(events).qa_metrics.model_dump()
        assert first == second
