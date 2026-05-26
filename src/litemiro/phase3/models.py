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


class QaMetrics(BaseModel):
    """OASIS 등가성 회귀 게이트용 결정적 수치 (#59).

    LLM 분석을 거치지 않는 순수 통계. 보고서는 본 값을 그대로 인용해 회귀 추적
    한다 (`docs/qa/metrics.md`). 정의 / 정규화 / OASIS 베이스라인 확보 상태는
    같은 문서 참조.

    * ``action_entropy_normalized`` — Shannon(P(action_type)) / log2(K). K = 6.
      0 = 한 액션만 / 1 = 균일. 낮으면 행동 다양성 결여 신호.
    * ``follow_clustering_coefficient`` — FOLLOW 이벤트로 재구성한 (방향성 무시)
      그래프의 평균 local clustering coefficient. 0 = 별 그래프 / 1 = 완전
      이웃성. 클러스터 형성이 안 보이면 echo chamber 없는 평탄 네트워크 신호.
    * ``content_word_entropy_normalized`` — CREATE_POST·QUOTE_POST 의 content 를
      공백 토크나이즈한 word frequency 의 Shannon / log2(|vocab|). 한국어 형태소
      분석 없이 어휘 다양성만 근사 — 진짜 토픽 entropy 는 RoundEvent 스키마에
      ``topics`` 필드 추가 후 별도 PR 에서 정확화.

    OASIS (arXiv:2411.11581) 는 위 3 메트릭에 대한 단일 베이스라인 수치를
    공개하지 않음 — 등가성 판단은 self-baseline (run-to-run 회귀) 으로 시작하고,
    OASIS 측 수치 확보 시 직접 비교로 승격 (`docs/qa/metrics.md`).
    """

    model_config = _FROZEN

    action_entropy_normalized: float = Field(ge=0.0, le=1.0)
    follow_clustering_coefficient: float = Field(ge=0.0, le=1.0)
    content_word_entropy_normalized: float = Field(ge=0.0, le=1.0)


class AggregationResult(BaseModel):
    """`DataAggregator` 산출 — 카테고리별 통계 dict + qa_metrics.

    ``categories`` 값은 ``Mapping[str, Any]`` 로 두어 카테고리 별 자유 스키마
    허용 (네트워크와 시계열의 키가 다르다). 파이프라인 다운스트림이 LLM
    프롬프트로 직렬화할 때 다시 정규화한다.

    ``qa_metrics`` 는 LLM 분석을 안 거치는 결정적 수치 — `QaMetrics` 참고.
    """

    model_config = _FROZEN

    n_events: int = Field(ge=0)
    n_agents: int = Field(ge=0)
    n_rounds: int = Field(ge=0)
    categories: Mapping[str, Mapping[str, Any]]
    qa_metrics: QaMetrics


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
    composer_primary_model: str = "openrouter/anthropic/claude-opus-4.7"
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
    "QaMetrics",
    "ReportConfig",
]
