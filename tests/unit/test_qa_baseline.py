"""litemiro.phase3.baseline 순수 함수 테스트 (LLM/IO 무관).

self-baseline 집계(평균±sigma)와 회귀 판정(±ksigma) 로직을 결정적 입력으로 고정한다.
실제 N 회 시뮬 실행은 scripts/collect_qa_baseline.py (LLM) 가 담당하고, 여기서는
그 산출을 다루는 로직만 검증한다.
"""

from __future__ import annotations

import statistics
from typing import Any

import pytest

from litemiro.phase3.baseline import (
    METRIC_NAMES,
    check_regression,
    extract_metrics,
    summarize_baseline,
)
from litemiro.phase3.models import AggregationResult, PhenomenaMetrics, QaMetrics


def _result(**ph_overrides: Any) -> AggregationResult:
    ph: dict[str, Any] = {
        "cascade_max_depth": 1,
        "cascade_max_breadth": 2,
        "cascade_max_scale": 3,
        "n_cascades": 1,
        "popularity_gini": 0.5,
    }
    ph.update(ph_overrides)
    return AggregationResult(
        n_events=1,
        n_agents=1,
        n_rounds=1,
        categories={
            "action_distribution": {},
            "network_metrics": {},
            "topic_flow": {},
            "time_series": {},
        },
        qa_metrics=QaMetrics(
            action_entropy_normalized=0.8,
            follow_clustering_coefficient=0.0,
            content_word_entropy_normalized=0.9,
        ),
        phenomena=PhenomenaMetrics(**ph),
    )


def _row(**overrides: float | None) -> dict[str, float | None]:
    base: dict[str, float | None] = dict.fromkeys(METRIC_NAMES, None)
    base.update(overrides)
    return base


class TestExtractMetrics:
    def test_flat_dict_covers_all_names(self) -> None:
        m = extract_metrics(_result())
        assert set(m) == set(METRIC_NAMES)
        assert m["action_entropy_normalized"] == 0.8
        assert m["cascade_max_depth"] == 1
        assert m["popularity_gini"] == 0.5

    def test_none_passthrough_for_missing_ontology(self) -> None:
        m = extract_metrics(_result(follow_ideology_gap=None, ideology_assortativity=None))
        assert m["follow_ideology_gap"] is None
        assert m["ideology_assortativity"] is None


class TestSummarizeBaseline:
    def test_mean_std_min_max(self) -> None:
        rows = [_row(popularity_gini=v) for v in (0.4, 0.6, 0.5)]
        s = summarize_baseline(rows)["popularity_gini"]
        assert s["n"] == 3
        assert s["mean"] == pytest.approx(0.5)
        assert s["min"] == 0.4
        assert s["max"] == 0.6
        assert s["std"] == pytest.approx(statistics.stdev([0.4, 0.6, 0.5]))

    def test_none_values_excluded(self) -> None:
        rows = [_row(popularity_gini=0.5), _row(), _row()]
        s = summarize_baseline(rows)
        assert s["popularity_gini"]["n"] == 1
        assert s["follow_ideology_gap"]["n"] == 0
        assert s["follow_ideology_gap"]["mean"] is None

    def test_single_sample_std_zero(self) -> None:
        s = summarize_baseline([_row(popularity_gini=0.7)])["popularity_gini"]
        assert s["n"] == 1
        assert s["std"] == 0.0


class TestCheckRegression:
    def _baseline(self, mean: float, std: float) -> dict[str, dict[str, float | int | None]]:
        empty: dict[str, float | int | None] = {
            "n": 0,
            "mean": None,
            "std": None,
            "min": None,
            "max": None,
        }
        out: dict[str, dict[str, float | int | None]] = {n: dict(empty) for n in METRIC_NAMES}
        out["popularity_gini"] = {"n": 5, "mean": mean, "std": std, "min": mean, "max": mean}
        return out

    def test_within_range_no_violation(self) -> None:
        assert check_regression(_row(popularity_gini=0.55), self._baseline(0.5, 0.1), 2.0) == []

    def test_outside_range_violation(self) -> None:
        v = check_regression(_row(popularity_gini=0.8), self._baseline(0.5, 0.1), 2.0)
        assert len(v) == 1
        assert v[0]["metric"] == "popularity_gini"
        assert v[0]["value"] == pytest.approx(0.8)

    def test_zero_std_metric_skipped(self) -> None:
        # 분산 0 (단일 표본/동일값) → 게이트 안 함, 값이 멀어도 위반 아님.
        assert check_regression(_row(popularity_gini=0.9), self._baseline(0.5, 0.0), 2.0) == []

    def test_none_current_value_skipped(self) -> None:
        assert check_regression(_row(), self._baseline(0.5, 0.1), 2.0) == []

    def test_boundary_is_inclusive(self) -> None:
        # 정확히 +2sigma (0.5 + 2*0.1 = 0.7) 는 범위 안.
        assert check_regression(_row(popularity_gini=0.7), self._baseline(0.5, 0.1), 2.0) == []
