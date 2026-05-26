"""``RealPlazaComposer`` — events.jsonl → Markdown 보고서 어댑터.

step 4 — Phase 3 의 `DataAggregator` → `PatternAnalyzer` → `ReportComposer`
파이프라인을 한 번에 감싼다. CLI (``litemiro-report``) 와 같은 컴포넌트를
공유하지만 HTTP 경로에서는 PlazaStore 가 호출자라서 protocol 로 추상화 —
fake 가 LLM 키 없이 들어올 수 있게 한다.

전체 실패 시 (Opus + Qwen 모두 사망) ``markdown=None`` 으로 폴백한다 —
``/report`` 가 통계 본문만 돌려주고 LLM 서술은 비운다. 500 보다 200 +
빈 markdown 이 클라이언트에 더 친절.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from litemiro.phase1.models import Preset
from litemiro.phase3.data_aggregator import DataAggregator
from litemiro.phase3.models import AggregationResult, ReportConfig
from litemiro.phase3.pattern_analyzer import PatternAnalyzer
from litemiro.phase3.report_composer import ReportComposer

if TYPE_CHECKING:
    from litemiro.interfaces import LLMClient

# Phase 3 `ReportConfig` 의 기본값을 단일 진실의 원천으로 삼는다 — composer 와
# CLI 가 같은 default slug 를 본다. 여기서 따로 리터럴을 박으면 drift 의 원인.
_DEFAULTS = ReportConfig()

_logger = structlog.get_logger(__name__)


@dataclass(frozen=True)
class ComposerOutcome:
    """``PlazaComposer.__call__`` 의 반환 — markdown + 회계.

    ``markdown`` 이 ``None`` 이면 LLM 단이 전부 실패한 폴백. 통계 응답은
    그대로 떨어지지만 자연어 서술은 비어 있다.

    ``aggregation`` 은 LLM 호출 직전에 컴퓨테한 결정적 집계 — store 가
    record 에 캐시해서 ``/report`` 매 호출마다 events.jsonl 재집계를 피한다.
    composer 가 미실행(events 없음) 이거나 캐싱 안 하는 fake 는 ``None``.
    """

    markdown: str | None
    tokens_used: int = 0
    fallback_used: bool = False
    aggregation: AggregationResult | None = None


class RealPlazaComposer:
    """실 LLM 으로 도는 composer — `LiteLLMClient` 를 받아 Phase 3 파이프라인을 돌린다.

    프리셋은 일단 ``quick`` 고정 (1 콜 analyzer + 1 콜 composer = 최소 2 콜).
    사용자가 preset 을 고르게 하는 건 후속 — CreatePlazaRequest 에 필드 추가
    + 클라이언트 미러링까지 같이 가야 한다.
    """

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        preset: Preset = Preset.QUICK,
        analyzer_model: str | None = None,
        composer_primary_model: str | None = None,
        composer_fallback_model: str | None = None,
    ) -> None:
        # 명시 인자 없으면 `ReportConfig` 의 default 를 그대로 — slug 가 한 곳에서만
        # 정의되도록.
        self._llm = llm_client
        self._config = ReportConfig(
            preset=preset,
            analyzer_model=analyzer_model or _DEFAULTS.analyzer_model,
            composer_primary_model=composer_primary_model or _DEFAULTS.composer_primary_model,
            composer_fallback_model=composer_fallback_model or _DEFAULTS.composer_fallback_model,
        )

    async def __call__(self, *, plaza_id: str, event_log_path: Path) -> ComposerOutcome:
        # events.jsonl 이 비어 있어도 (--fake 또는 0-round) 빈 집계로 폴백 —
        # 통계 응답은 떨어지지만 markdown 은 None 으로 비운다. 단일 stat
        # syscall 이라 async 로 감쌀 가치 없음 — `litemiro-report` CLI 도 동일.
        if not event_log_path.exists():  # noqa: ASYNC240
            _logger.info(
                "plaza_composer.skip_no_events",
                plaza_id=plaza_id,
                path=str(event_log_path),
            )
            return ComposerOutcome(markdown=None)

        aggregation = DataAggregator.aggregate(event_log_path)
        analyzer = PatternAnalyzer(llm=self._llm)
        composer = ReportComposer(llm=self._llm)
        insights = await analyzer.analyze(result=aggregation, config=self._config)
        try:
            report = await composer.compose(
                result=aggregation, insights=insights, config=self._config
            )
        except Exception as exc:
            # ReportComposer 의 primary+fallback 모두 실패. 통계는 떨어지므로
            # 500 으로 죽이지 말고 markdown=None 으로 폴백한다.
            _logger.warning(
                "plaza_composer.report_failed",
                plaza_id=plaza_id,
                error=str(exc),
            )
            return ComposerOutcome(
                markdown=None,
                tokens_used=sum(item.tokens_used for item in insights.items),
                fallback_used=False,
                aggregation=aggregation,
            )
        return ComposerOutcome(
            markdown=report.markdown,
            tokens_used=sum(item.tokens_used for item in insights.items) + report.tokens_used,
            fallback_used=report.fallback_used,
            aggregation=aggregation,
        )


__all__ = ["ComposerOutcome", "RealPlazaComposer"]
