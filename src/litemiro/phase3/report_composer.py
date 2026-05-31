"""`ReportComposer` — `PartialInsights` → Markdown 보고서.

Phase 3 메모리 노트의 모델 라우팅 + PRD §4.3 의 재시도 정책:
Claude Opus 가 1 차이며 ``tenacity`` 재시도 1 회 (총 2 회 시도) 까지 시도하고,
그래도 실패하면 Qwen-plus 가 폴백한다. 폴백 사용 여부는
``ComposedReport.fallback_used`` 로 노출해 비용 회계에 사용한다. 폴백마저
실패하면 그대로 전파한다.

LLM 출력은 그대로 본문이 된다 — 후처리 / PDF 변환은 후속 이슈.
"""

from __future__ import annotations

import json

import structlog
from pydantic import BaseModel, ConfigDict, Field
from tenacity import AsyncRetrying, stop_after_attempt, wait_none

from litemiro.interfaces import LLMClient
from litemiro.models import LLMResponse
from litemiro.phase3.models import (
    AggregationResult,
    PartialInsights,
    ReportConfig,
)

_logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "당신은 가상 인격들이 한 이슈를 두고 벌인 광장 토론을 읽고 '여론 예측' 보고서를 쓰는 "
    "분석가다. 이 시뮬레이션은 업로드된 이슈 자료에 대해 수십~수백 명의 가상 인격이 토론한 "
    "결과이며, 보고서의 목적은 그 토론이 도달한 여론을 예측해 전달하는 것이다 — 독자가 알고 "
    "싶은 것은 '이 이슈의 여론이 어떻게 될까' 이지 시뮬레이션의 메타 통계가 아니다. "
    "한국어 Markdown 으로 작성하라 — 문서 제목은 `#`, 주요 섹션은 `##`, 세부는 `###`. "
    "다음 5 섹션을 순서대로 포함하라: "
    "(1) 핵심 여론 예측 — 이슈에 대해 가상 여론이 도달한 결론적 입장과 온도(지지·반대·유보의 "
    "전반 기류)를 첫머리에 단정적으로 제시한다. "
    "(2) 입장 분포 — 찬성·반대·중립이 어떻게 갈렸는지를 게시물 논조로 가늠하고, ideology 동질성"
    "(`follow_ideology_gap`·`ideology_assortativity`)으로 양극화 정도를 함께 짚는다. "
    "(3) 주요 논점 — `categories.topic_flow.samples` 의 실제 게시물·인용 본문에서 등장한 "
    "쟁점과 표현을 작성자 id 와 함께 직접 인용해 어떤 주장들이 부딪쳤는지 보인다. "
    "(4) 여론 주도·확산 — 누가 여론을 끌었는지(상위 작성자·피팔로우 노드)와 메시지가 어떻게 "
    "번졌는지(`cascade_*` 깊이·규모, 인기 집중 `popularity_gini`·`early_mover_share`)를 서술한다. "
    "(5) 신뢰도와 한계 — 표본 규모·라운드 수·활성도(DO_NOTHING)·prompt 한계로 이 예측을 "
    "얼마나 신뢰할 수 있는지, 무엇이 관측되지 않았는지 밝힌다. "
    "행동 분포·네트워크 수치는 여론 자체가 아니라 (4)(5) 의 근거로만 쓰고 보고서를 메타 통계 "
    "나열로 만들지 말라. 수치 비교가 잦으면 표(`|`)로 정리해도 좋다. "
    "주어진 통계·게시물·분석가 인사이트만을 근거로 하며, 데이터에 없는 사실은 절대 지어내지 "
    "않고 관측되지 않은 항목은 '관측되지 않음' 으로 명시한다. ideology·진영 지표가 None 이면 "
    "입장 분포는 게시물 논조로만 정성 판단하고 그 한계를 밝힌다."
)


class ComposedReport(BaseModel):
    """`ReportComposer.compose` 결과 — 본문 + 회계."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    markdown: str
    model: str
    fallback_used: bool = False
    tokens_used: int = Field(default=0, ge=0)


class ReportComposer:
    def __init__(self, *, llm: LLMClient, primary_max_attempts: int = 2) -> None:
        # primary_max_attempts=2 → 첫 시도 + 재시도 1 회 (PRD §4.3).
        if primary_max_attempts < 1:
            raise ValueError(f"primary_max_attempts must be >= 1, got {primary_max_attempts}")
        self._llm = llm
        self._primary_max_attempts = primary_max_attempts

    async def compose(
        self,
        *,
        result: AggregationResult,
        insights: PartialInsights,
        config: ReportConfig,
    ) -> ComposedReport:
        user = _build_user_prompt(result, insights)
        try:
            response = await self._call_primary(
                system=_SYSTEM_PROMPT, user=user, model=config.composer_primary_model
            )
        except Exception as exc:
            _logger.warning(
                "report_composer_primary_failed",
                primary_model=config.composer_primary_model,
                fallback_model=config.composer_fallback_model,
                attempts=self._primary_max_attempts,
                error=str(exc),
            )
            response = await self._llm.complete(
                system=_SYSTEM_PROMPT,
                user=user,
                model=config.composer_fallback_model,
            )
            return ComposedReport(
                markdown=response.content,
                model=config.composer_fallback_model,
                fallback_used=True,
                tokens_used=response.prompt_tokens + response.completion_tokens,
            )
        return ComposedReport(
            markdown=response.content,
            model=config.composer_primary_model,
            fallback_used=False,
            tokens_used=response.prompt_tokens + response.completion_tokens,
        )

    async def _call_primary(self, *, system: str, user: str, model: str) -> LLMResponse:
        async for attempt in AsyncRetrying(
            stop=stop_after_attempt(self._primary_max_attempts),
            wait=wait_none(),
            reraise=True,
        ):
            with attempt:
                return await self._llm.complete(system=system, user=user, model=model)
        raise RuntimeError("AsyncRetrying terminated without success or reraise")


def _build_user_prompt(result: AggregationResult, insights: PartialInsights) -> str:
    """카테고리 raw 통계 + QA 지표 + 분석가 인사이트를 한 번에 composer 에 전달.

    이전 구현은 ``n_events / n_agents / n_rounds`` 와 ``CategoryInsight.summary``
    텍스트만 넘겼다 — 분석가가 짚지 못한 상위 행위자·라운드별 추이·QaMetrics 가
    composer 시야에서 사라져 보고서가 얇아졌다. 본 함수는 ``DataAggregator`` 가
    이미 만들어 둔 풍부한 카테고리 dict 와 ``QaMetrics`` 를 JSON 으로 함께
    실어, composer 가 표·글머리표 형태로 풀어낼 재료를 확보하게 한다.
    """

    payload = {
        "scope": {
            "n_events": result.n_events,
            "n_agents": result.n_agents,
            "n_rounds": result.n_rounds,
        },
        "qa_metrics": {
            "action_entropy_normalized": result.qa_metrics.action_entropy_normalized,
            "follow_clustering_coefficient": result.qa_metrics.follow_clustering_coefficient,
            "content_word_entropy_normalized": result.qa_metrics.content_word_entropy_normalized,
        },
        "phenomena": {
            "cascade_max_depth": result.phenomena.cascade_max_depth,
            "cascade_max_breadth": result.phenomena.cascade_max_breadth,
            "cascade_max_scale": result.phenomena.cascade_max_scale,
            "n_cascades": result.phenomena.n_cascades,
            "follow_ideology_gap": result.phenomena.follow_ideology_gap,
            "ideology_assortativity": result.phenomena.ideology_assortativity,
            "popularity_gini": result.phenomena.popularity_gini,
            "early_mover_share": result.phenomena.early_mover_share,
        },
        "categories": {cat: dict(data) for cat, data in result.categories.items()},
    }

    lines = [
        "다음은 한 이슈를 두고 가상 인격들이 벌인 광장 토론의 집계 결과다. 이를 바탕으로 "
        "그 이슈에 대한 '여론 예측' 보고서를 한국어 Markdown 으로 작성하라.",
        "",
        "## 규모",
        f"- 이벤트 수: {result.n_events}",
        f"- 에이전트 수: {result.n_agents}",
        f"- 라운드 수: {result.n_rounds}",
        "",
        "## 카테고리별 분석가 인사이트",
    ]
    for item in insights.items:
        lines.append(f"### {item.category}")
        lines.append(item.summary)
        lines.append("")
    lines.append("## 원시 통계·게시물·현상 지표 (JSON)")
    lines.append(
        "여론 예측의 1 차 재료는 `categories.topic_flow.samples` 의 실제 게시물 본문이다 "
        "— 작성자 id 와 함께 인용해 어떤 주장이 오갔는지 보여라. `phenomena` 의 "
        "`follow_ideology_gap`·`ideology_assortativity` 는 양극화를, `cascade_*`·"
        "`popularity_gini`·`early_mover_share` 는 확산과 여론 주도 집중을, action/network "
        "통계는 신뢰도·활성도 근거로 쓴다. 분포별 집중도(*_concentration: n_unique·"
        "top5_share·gini)도 함께 인용할 수 있다. n_amplifications 는 REPOST 건수와 같은 "
        "값(본문 없는 증폭)이며 QUOTE_POST 와 구분한다."
    )
    lines.append("```json")
    lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    lines.append("```")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["ComposedReport", "ReportComposer"]
