"""positions SSE 의 engagement 집계 + 페이로드 직렬화 단위 테스트.

라이브 화면 산점도(라운드별 에이전트 위치) 의 x/y/size 가 어떻게 만들어지는지
함수 레벨로 못박는다:

- ``_read_engagement(max_round=N)`` — 라운드 누적 필터 (None 이면 기존 전체 동작).
- ``_positions_payload`` — belief_trajectory 한 줄 → SSE data shape/값.
- ``PlazaStore.load_latest_positions`` — belief_trajectory.jsonl 마지막 ideology 줄.

전부 작은 합성 belief_trajectory + events 픽스처라 외부 의존이 없다.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from litemiro.api.engagement import _read_engagement
from litemiro.api.store import (
    PlazaRecord,
    PlazaStore,
    RunnerOutcome,
    _load_latest_positions,
    _positions_payload,
)


def _write_events(path: Path, events: list[tuple[int, str, dict[str, str]]]) -> None:
    """(round_num, agent_id, action_dict) → events.jsonl 본문."""
    lines = [
        json.dumps(
            {
                "round_num": round_num,
                "timestamp": "2026-05-26T00:00:00+00:00",
                "agent_id": agent_id,
                "action": action,
            }
        )
        for round_num, agent_id, action in events
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_belief(path: Path, rounds: list[tuple[int, dict[str, float]]]) -> None:
    """헤더 줄 + (round_num, ideology_map) 줄들로 belief_trajectory.jsonl 작성."""
    lines = [json.dumps({"schema": "belief_trajectory/v1"})]
    lines += [
        json.dumps({"ideology": ideology, "round_num": round_num}) for round_num, ideology in rounds
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


class TestReadEngagementMaxRound:
    """``_read_engagement`` 의 ``max_round`` 누적 필터."""

    def test_max_round_filters_later_rounds(self, tmp_path: Path) -> None:
        """``round_num <= max_round`` 인 라인만 집계된다."""
        events_path = tmp_path / "events.jsonl"
        # a01_r0000 작성자 a01. r0 에서 LIKE(1), r1 에서 REPOST(2), r2 에서 QUOTE(3).
        _write_events(
            events_path,
            [
                (0, "a02", {"type": "LIKE_POST", "target_post_id": "a01_r0000"}),
                (1, "a03", {"type": "REPOST", "target_post_id": "a01_r0000"}),
                (2, "a02", {"type": "QUOTE_POST", "target_post_id": "a01_r0000", "content": ".."}),
            ],
        )
        # max_round=0 → LIKE 만 → a01 influence 1, activity a02=1.
        _f, infl0, act0 = _read_engagement(events_path, max_round=0)
        assert infl0 == {"a01": 1}
        assert act0 == {"a02": 1}
        # max_round=1 → LIKE+REPOST → a01 = 1+2 = 3, activity a02=1, a03=1.
        _f, infl1, act1 = _read_engagement(events_path, max_round=1)
        assert infl1 == {"a01": 3}
        assert act1 == {"a02": 1, "a03": 1}

    def test_none_matches_full_pass(self, tmp_path: Path) -> None:
        """``max_round=None`` 은 전체 집계 — 인자 없는 기존 호출과 동일."""
        events_path = tmp_path / "events.jsonl"
        _write_events(
            events_path,
            [
                (0, "a02", {"type": "LIKE_POST", "target_post_id": "a01_r0000"}),
                (1, "a03", {"type": "REPOST", "target_post_id": "a01_r0000"}),
                (2, "a02", {"type": "QUOTE_POST", "target_post_id": "a01_r0000", "content": ".."}),
            ],
        )
        default_result = _read_engagement(events_path)
        none_result = _read_engagement(events_path, max_round=None)
        assert default_result == none_result
        _f, infl, act = default_result
        # 전체: a01 = 1+2+3 = 6, activity a02=2, a03=1.
        assert infl == {"a01": 6}
        assert act == {"a02": 2, "a03": 1}

    def test_follow_counts_respect_max_round(self, tmp_path: Path) -> None:
        """FOLLOW 의 follower/influence 도 ``max_round`` 를 따른다."""
        events_path = tmp_path / "events.jsonl"
        _write_events(
            events_path,
            [
                (0, "a02", {"type": "FOLLOW", "target_agent_id": "a01"}),
                (3, "a03", {"type": "FOLLOW", "target_agent_id": "a01"}),
            ],
        )
        follower, infl, _act = _read_engagement(events_path, max_round=0)
        assert follower == {"a01": 1}
        assert infl == {"a01": 5}


class TestPositionsPayload:
    """``_positions_payload`` 의 shape/값."""

    def test_payload_shape_and_values(self, tmp_path: Path) -> None:
        """x=ideology, y=누적 influence, size=누적 activity, color 미포함."""
        events_path = tmp_path / "events.jsonl"
        # round 1 시점까지: a01 이 LIKE(1)+REPOST(2)=3 받음. a02 activity=1, a03 activity=1.
        _write_events(
            events_path,
            [
                (0, "a02", {"type": "LIKE_POST", "target_post_id": "a01_r0000"}),
                (1, "a03", {"type": "REPOST", "target_post_id": "a01_r0000"}),
                # round 2 — max_round=1 이면 집계에서 빠져야 함.
                (2, "a02", {"type": "QUOTE_POST", "target_post_id": "a01_r0000", "content": ".."}),
            ],
        )
        ideology = {"a01": 0.8, "a02": 0.2, "a03": 0.5}
        payload = _positions_payload(1, ideology, events_path)
        assert payload["round_num"] == 1
        by_id = {a["id"]: a for a in payload["agents"]}
        assert set(by_id) == {"a01", "a02", "a03"}
        # x = 그 라운드 ideology.
        assert by_id["a01"]["x"] == 0.8
        assert by_id["a02"]["x"] == 0.2
        # y = max_round=1 누적 influence — a01=3, 나머지 0.
        assert by_id["a01"]["y"] == 3
        assert by_id["a02"]["y"] == 0
        assert by_id["a03"]["y"] == 0
        # size = max_round=1 누적 activity — a02=1, a03=1, a01=0.
        assert by_id["a02"]["size"] == 1
        assert by_id["a03"]["size"] == 1
        assert by_id["a01"]["size"] == 0
        # 색(stance) 은 미포함 — 키가 id/x/y/size 뿐.
        assert set(by_id["a01"]) == {"id", "x", "y", "size"}

    def test_missing_agent_defaults_to_zero(self, tmp_path: Path) -> None:
        """events 에 등장 안 한 에이전트는 y/size 0."""
        events_path = tmp_path / "events.jsonl"
        _write_events(events_path, [])  # 빈 events.
        payload = _positions_payload(0, {"a01": 0.1}, events_path)
        agent = payload["agents"][0]
        assert agent == {"id": "a01", "x": 0.1, "y": 0, "size": 0}


class TestLoadLatestPositions:
    """``_load_latest_positions`` + ``PlazaStore.load_latest_positions``."""

    def test_picks_last_ideology_line(self, tmp_path: Path) -> None:
        """마지막 ideology 줄의 round_num/ideology 가 페이로드에 반영된다."""
        belief_path = tmp_path / "belief_trajectory.jsonl"
        events_path = tmp_path / "events.jsonl"
        _write_belief(
            belief_path,
            [
                (0, {"a01": 0.5, "a02": 0.5}),
                (1, {"a01": 0.6, "a02": 0.4}),
                (2, {"a01": 0.7, "a02": 0.3}),
            ],
        )
        _write_events(
            events_path,
            [(0, "a02", {"type": "LIKE_POST", "target_post_id": "a01_r0000"})],
        )
        payload = _load_latest_positions(belief_path, events_path)
        assert payload is not None
        assert payload["round_num"] == 2
        by_id = {a["id"]: a for a in payload["agents"]}
        assert by_id["a01"]["x"] == 0.7
        assert by_id["a02"]["x"] == 0.3
        # 누적 influence (max_round=2): a01 LIKE = 1.
        assert by_id["a01"]["y"] == 1

    def test_missing_file_returns_none(self, tmp_path: Path) -> None:
        assert _load_latest_positions(tmp_path / "nope.jsonl", tmp_path / "events.jsonl") is None

    def test_header_only_returns_none(self, tmp_path: Path) -> None:
        """ideology 줄이 하나도 없으면 (헤더만) None."""
        belief_path = tmp_path / "belief_trajectory.jsonl"
        belief_path.write_text(
            json.dumps({"schema": "belief_trajectory/v1"}) + "\n", encoding="utf-8"
        )
        assert _load_latest_positions(belief_path, tmp_path / "events.jsonl") is None

    def test_store_method(self, tmp_path: Path) -> None:
        """``PlazaStore.load_latest_positions`` 가 belief/events 를 함께 읽는다."""

        async def _noop_runner(
            *,
            plaza_id: str,
            ontology_a_path: Path,
            ontology_b_path: Path,
            rounds: int,
            event_log_path: Path,
            checkpoint_dir: Path,
            on_progress: object,
        ) -> RunnerOutcome:
            return RunnerOutcome()

        store = PlazaStore(runner=_noop_runner, base_dir=tmp_path)  # type: ignore[arg-type]
        plaza_root = tmp_path / "p1"
        plaza_root.mkdir()
        events_path = plaza_root / "events.jsonl"
        belief_path = plaza_root / "belief_trajectory.jsonl"
        _write_belief(belief_path, [(0, {"a01": 0.3}), (1, {"a01": 0.9})])
        _write_events(
            events_path,
            [(0, "a02", {"type": "REPOST", "target_post_id": "a01_r0000"})],
        )
        record = PlazaRecord(
            plaza_id="p1",
            status="completed",
            rounds_total=2,
            event_log_path=events_path,
        )

        async def _run() -> dict[str, object] | None:
            async with store._lock:
                store._records["p1"] = record
            return await store.load_latest_positions("p1")

        payload = asyncio.run(_run())
        assert payload is not None
        assert payload["round_num"] == 1
        agent = payload["agents"][0]
        assert agent["x"] == 0.9
        assert agent["y"] == 2  # REPOST weight.

    def test_store_method_none_when_no_event_log(self, tmp_path: Path) -> None:
        """``event_log_path`` 가 None (fake) 이면 None."""

        async def _noop_runner(**_kwargs: object) -> RunnerOutcome:
            return RunnerOutcome()

        store = PlazaStore(runner=_noop_runner, base_dir=tmp_path)  # type: ignore[arg-type]
        record = PlazaRecord(
            plaza_id="p2",
            status="completed",
            rounds_total=1,
            event_log_path=None,
        )

        async def _run() -> dict[str, object] | None:
            async with store._lock:
                store._records["p2"] = record
            return await store.load_latest_positions("p2")

        assert asyncio.run(_run()) is None
