"""Phase 3 Pydantic 모델 — 컴포넌트 간 경계 계약."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from litemiro.phase1.models import Preset

_FROZEN: ConfigDict = ConfigDict(extra="forbid", frozen=True)


CATEGORY_ACTION_DISTRIBUTION = "action_distribution"
CATEGORY_NETWORK_METRICS = "network_metrics"
CATEGORY_TOPIC_FLOW = "topic_flow"
CATEGORY_TIME_SERIES = "time_series"

CATEGORIES: tuple[str, ...] = (
    CATEGORY_ACTION_DISTRIBUTION,
    CATEGORY_NETWORK_METRICS,
    CATEGORY_TOPIC_FLOW,
    CATEGORY_TIME_SERIES,
)


class AggregationResult(BaseModel):
    """`DataAggregator` 산출 — 카테고리별 통계 dict.

    값은 ``Mapping[str, Any]`` 로 두어 카테고리 별 자유 스키마 허용
    (네트워크와 시계열의 키가 다르다). 파이프라인 다운스트림이 LLM
    프롬프트로 직렬화할 때 다시 정규화한다.
    """

    model_config = _FROZEN

    n_events: int = Field(ge=0)
    n_agents: int = Field(ge=0)
    n_rounds: int = Field(ge=0)
    categories: Mapping[str, Mapping[str, Any]]


class CategoryInsight(BaseModel):
    """단일 카테고리의 LLM 분석 결과."""

    model_config = _FROZEN

    category: str
    summary: str
    model: str
    tokens_used: int = Field(ge=0)


class PartialInsights(BaseModel):
    """`PatternAnalyzer` 산출 — 카테고리별 인사이트 모음."""

    model_config = _FROZEN

    items: tuple[CategoryInsight, ...]

    def by_category(self) -> dict[str, CategoryInsight]:
        return {item.category: item for item in self.items}


class ReportConfig(BaseModel):
    """파이프라인 입력 설정.

    프리셋이 호출 수 / 청킹 단위를 결정한다 (Phase 3 메모리 노트):
    - quick: 카테고리 전체를 1 회 묶어서 호출 → 1 회
    - standard: 카테고리 4 개를 1 회씩 → 4 회
    - full: 카테고리당 2 회 분석 (다른 lens) → 8 회

    모델 ID 는 LiteLLM/OpenRouter 명명을 그대로 사용.
    """

    model_config = _FROZEN

    preset: Preset = Preset.QUICK
    analyzer_model: str = "openrouter/qwen/qwen-plus"
    composer_primary_model: str = "openrouter/anthropic/claude-opus-4"
    composer_fallback_model: str = "openrouter/qwen/qwen-plus"


__all__ = [
    "CATEGORIES",
    "CATEGORY_ACTION_DISTRIBUTION",
    "CATEGORY_NETWORK_METRICS",
    "CATEGORY_TIME_SERIES",
    "CATEGORY_TOPIC_FLOW",
    "AggregationResult",
    "CategoryInsight",
    "PartialInsights",
    "ReportConfig",
]
