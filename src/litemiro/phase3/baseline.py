"""self-baseline 집계·회귀 판정 — 순수 함수 (LLM/IO 무관, 테스트 가능).

`scripts/collect_qa_baseline.py` 와 `scripts/check_qa_regression.py` 가 CLI
wrapper 로 이 함수들을 쓴다. OASIS 가 단일 비교 베이스라인을 주지 않으므로
(`docs/qa/metrics.md`) 등가성 판단은 self-baseline (run-to-run 회귀) 으로 한다:
같은 ontology 로 seed 만 바꿔 N 회 돌린 분포의 평균±sigma 를 기준으로, 코드 변경 후
재실행이 ±ksigma 범위를 벗어나면 회귀로 본다.

게이트 대상은 `QaMetrics` 2 종 + `PhenomenaMetrics` 8 종을 한 평면 dict 로 펴서
다룬다. ontology 없이 집계된 양극화 메트릭(None)은 통계에서 제외 — 분포가 비면 그
메트릭은 게이트하지 않는다.
"""

from __future__ import annotations

import statistics

from litemiro.phase3.models import AggregationResult

# baseline.json 구조 버전 — 메트릭 키를 추가/제거하면 올린다. check_qa_regression
# 이 불일치를 경고해 스키마 드리프트로 인한 silent 실패를 막는다.
BASELINE_SCHEMA = "1"

# 베이스라인이 게이트하는 메트릭 평면 키 — QaMetrics 2 + PhenomenaMetrics 8.
# follow_clustering_coefficient 는 현 규모에서 신호가 없어 deprecated (metrics.md):
# 모델 필드·계산·보고서 인용은 스키마 안정을 위해 유지하되 회귀 게이트에선 뺀다.
METRIC_NAMES: tuple[str, ...] = (
    "action_entropy_normalized",
    "content_word_entropy_normalized",
    "cascade_max_depth",
    "cascade_max_breadth",
    "cascade_max_scale",
    "n_cascades",
    "follow_ideology_gap",
    "ideology_assortativity",
    "popularity_gini",
    "early_mover_share",
)


def extract_metrics(result: AggregationResult) -> dict[str, float | None]:
    """`AggregationResult` → 메트릭 평면 dict. None 은 그대로 (ontology 없는
    양극화 메트릭 등) — 통계 단계에서 제외된다."""
    qa = result.qa_metrics
    ph = result.phenomena
    return {
        "action_entropy_normalized": qa.action_entropy_normalized,
        "content_word_entropy_normalized": qa.content_word_entropy_normalized,
        "cascade_max_depth": ph.cascade_max_depth,
        "cascade_max_breadth": ph.cascade_max_breadth,
        "cascade_max_scale": ph.cascade_max_scale,
        "n_cascades": ph.n_cascades,
        "follow_ideology_gap": ph.follow_ideology_gap,
        "ideology_assortativity": ph.ideology_assortativity,
        "popularity_gini": ph.popularity_gini,
        "early_mover_share": ph.early_mover_share,
    }


def summarize_baseline(
    rows: list[dict[str, float | None]],
) -> dict[str, dict[str, float | int | None]]:
    """N 회 run 의 메트릭 행들 → 메트릭별 {n, mean, std, min, max}.

    None 은 제외하고 집계한다 (분포가 비면 n=0, mean/std=None). 표본 1 개면
    표준편차가 정의되지 않으므로 std=0.0 — 회귀 게이트는 std<=0 을 건너뛴다.
    """
    summary: dict[str, dict[str, float | int | None]] = {}
    for name in METRIC_NAMES:
        vals = [r[name] for r in rows if r.get(name) is not None]
        floats = [float(v) for v in vals if v is not None]
        if not floats:
            summary[name] = {"n": 0, "mean": None, "std": None, "min": None, "max": None}
            continue
        summary[name] = {
            "n": len(floats),
            "mean": statistics.fmean(floats),
            "std": statistics.stdev(floats) if len(floats) >= 2 else 0.0,
            "min": min(floats),
            "max": max(floats),
        }
    return summary


def check_regression(
    now: dict[str, float | None],
    baseline: dict[str, dict[str, float | int | None]],
    sigma: float = 2.0,
) -> list[dict[str, float | str]]:
    """현재 run 메트릭이 baseline ±sigma·sigma 를 벗어난 항목 리스트.

    분산 0 (std<=0: 단일 표본이거나 모든 run 동일) 인 메트릭은 게이트하지 않는다
    — 범위가 점이라 부동소수만으로도 깨져 의미가 없다. baseline 에 없거나 현재
    값이 None 인 메트릭도 건너뛴다. 빈 리스트 = 회귀 없음.
    """
    violations: list[dict[str, float | str]] = []
    for name in METRIC_NAMES:
        stat = baseline.get(name)
        if not stat or stat.get("mean") is None:
            continue
        std = stat.get("std") or 0.0
        if std <= 0.0:
            continue
        cur = now.get(name)
        if cur is None:
            continue
        mean = float(stat["mean"])  # type: ignore[arg-type]  # mean None 은 위에서 거름
        low = mean - sigma * float(std)
        high = mean + sigma * float(std)
        if not (low <= cur <= high):
            violations.append(
                {
                    "metric": name,
                    "value": float(cur),
                    "mean": mean,
                    "std": float(std),
                    "low": low,
                    "high": high,
                }
            )
    return violations


__all__ = [
    "BASELINE_SCHEMA",
    "METRIC_NAMES",
    "check_regression",
    "extract_metrics",
    "summarize_baseline",
]
