"""``PlazaReport`` 빌더 — events.jsonl → ``PlazaReportResponse``.

``DataAggregator.aggregate`` 결과 + (step 4) ``PlazaStore`` 가 미리 채워둔
LLM Markdown 본문을 합쳐 응답을 만든다. 같은 events.jsonl + 같은 markdown
은 항상 같은 응답 (LLM 호출은 본 모듈 바깥에서 이미 끝남 — store 의 _drive).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from litemiro.api.models import PlazaReportResponse
from litemiro.phase3.data_aggregator import DataAggregator

if TYPE_CHECKING:
    from litemiro.api.store import PlazaRecord


def build_report(record: PlazaRecord) -> PlazaReportResponse:
    """완료된 plaza 의 events.jsonl 을 집계해 응답 모델로 빌드.

    호출자가 status == completed 인지 검사한 뒤 호출해야 한다 — 본 함수는
    파일이 비어 있어도 (``DataAggregator`` 가 빈 결과를 돌려줌) 그대로 응답한다.
    파일이 아예 없는 경우 (``--fake`` 모드에서 진짜 시뮬을 안 돌린 경우, 또는
    runner 가 0 round 로 끝난 경우) 도 빈 집계로 폴백 — 500 보다 200 + 빈
    결과가 클라이언트에 더 친절.
    """
    if record.event_log_path is None:
        raise ValueError(f"plaza {record.plaza_id!r} has no event_log_path")
    # composer 가 한 번 돌았으면 record 캐시 사용 — events.jsonl 재집계 회피.
    # 없으면 (fake 경로 / composer 미실행) lazy 로 한 번 컴퓨테해 캐싱.
    if record.aggregation_cache is not None:
        aggregation = record.aggregation_cache
    elif record.event_log_path.exists():
        aggregation = DataAggregator.aggregate(record.event_log_path)
        record.aggregation_cache = aggregation
    else:
        aggregation = DataAggregator.aggregate_events([])
        record.aggregation_cache = aggregation
    return PlazaReportResponse(
        plaza_id=record.plaza_id,
        label=record.label,
        status=record.status,
        rounds_total=record.rounds_total,
        rounds_done=record.rounds_done,
        tokens_used=record.tokens_used,
        n_events=aggregation.n_events,
        n_agents=aggregation.n_agents,
        n_rounds=aggregation.n_rounds,
        # Pydantic 의 frozen mapping 을 mutable dict 로 직렬화 — FastAPI 응답 시
        # JSON 변환을 단순화하기 위함.
        categories={k: dict(v) for k, v in aggregation.categories.items()},
        qa_metrics=aggregation.qa_metrics.model_dump(),
        report_markdown=record.report_markdown,
        report_fallback_used=record.report_fallback_used,
    )


__all__ = ["build_report"]
