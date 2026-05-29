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


class PhenomenaMetrics(BaseModel):
    """OASIS (arXiv:2411.11581) 3 현상의 결정적 프록시 — 정보 확산·집단 양극화·
    herd 효과. `docs/qa/metrics.md`.

    `QaMetrics` 와 분리한 이유: cascade 메트릭은 [0,1] 정규화가 아니라 정수
    카운트라 `QaMetrics` 의 `Field(le=1.0)` 규약과 안 맞는다. 양극화 메트릭은
    ontology 의 `ideology` 가 있어야 계산되므로, ontology 없이 집계하면 `None`
    (하위호환 — 기존 `aggregate(jsonl_path)` 단일 인자 호출이 그대로 동작).

    OASIS 는 LLM 평가·실세계 RMSE 를 쓰지만 우리는 재현성을 위해 전부 LLM 없는
    결정적 계산이다 — 같은 입력은 같은 값.

    * `cascade_*` — REPOST/QUOTE `target_post_id` 체인으로 재구성한 전파 트리.
      depth=재게시 체인 최대 깊이, breadth=한 포스트의 최대 직접 재게시 수,
      scale=한 캐스케이드에 참여한 고유 에이전트 수. n_cascades=재게시가 1건
      이상 달린 원본 포스트 수 (표본 크기 — 작으면 depth/breadth 해석 주의).
    * `follow_ideology_gap` — FOLLOW 엣지의 평균 |Δideology| ([0,1]). 낮을수록
      비슷한 성향끼리 follow (호모필리 = 양극화 신호). ontology 없으면 None.
    * `ideology_assortativity` — follow 네트워크의 ideology Pearson 상관
      ([-1,1]). 양수=동질 선호. 엣지<2 또는 분산 0 이면 None.
    * `popularity_gini` — 피팔로우 수 분포의 지니 (#153 followee gini 승격).
      herd = 인기 노드 쏠림. 1=완전 집중, 0=균등.
    * `early_mover_share` — 전반부 라운드 상위 피팔로우 노드가 후반부 FOLLOW 의
      몇 비율을 흡수하는가 ([0,1]). 높을수록 "이미 인기있는 노드를 더 follow"
      하는 herd. 라운드<2 또는 후반부 FOLLOW 0 이면 None.
    """

    model_config = _FROZEN

    cascade_max_depth: int = Field(ge=0)
    cascade_max_breadth: int = Field(ge=0)
    cascade_max_scale: int = Field(ge=0)
    n_cascades: int = Field(ge=0)
    follow_ideology_gap: float | None = Field(default=None, ge=0.0, le=1.0)
    ideology_assortativity: float | None = Field(default=None, ge=-1.0, le=1.0)
    popularity_gini: float = Field(ge=0.0, le=1.0)
    early_mover_share: float | None = Field(default=None, ge=0.0, le=1.0)


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
    phenomena: PhenomenaMetrics


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
    "PhenomenaMetrics",
    "QaMetrics",
    "ReportConfig",
]
