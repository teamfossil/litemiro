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
    "당신은 시뮬레이션 결과를 정리해 분석 보고서를 작성하는 어시스턴트다. "
    "한국어 Markdown 으로 작성하라 — 문서 제목은 `#`, 주요 섹션은 `##`, 세부 항목은 `###`. "
    "다음 섹션을 반드시 포함하되 통계가 풍부한 영역에는 단락과 글머리표를 충분히 늘려라: "
    "(1) 규모 개요, (2) 행동 분포 분석, (3) 네트워크/팔로우 동학, (4) 시간 흐름과 활동 추이, "
    "(5) 주제·콘텐츠 흐름, (6) QA 지표 및 한계, (7) 종합 요약 및 시사점. "
    "수치 비교가 잦은 항목은 표(`|`) 로 정리하고, 상위 행위자/포스트/팔로우 대상은 "
    "에이전트 id 와 함께 글머리표로 나열하라. "
    "주어진 통계와 카테고리별 인사이트만을 근거로 작성하며, 카테고리 인사이트의 핵심 문장은 "
    "본문에 흡수해 반복 인용을 피하라. 데이터에 없는 사실은 절대 만들지 않으며, "
    "관측되지 않은 항목은 '관측되지 않음' 으로 명시한다. "
    "단정적인 분석 어조를 유지하되, 단어를 아끼려 핵심 수치를 누락하지는 말라."
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
        "categories": {cat: dict(data) for cat, data in result.categories.items()},
    }

    lines = [
        "다음 시뮬레이션 결과를 한국어 Markdown 보고서로 정리하라.",
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
    lines.append("## 원시 통계 (JSON)")
    lines.append(
        "분석가 인사이트가 빠뜨린 디테일 — 상위 행위자, 라운드별 추이, 분포별 "
        "집중도(*_concentration: n_unique·top5_share·gini), QaMetrics 등 — 은 아래 "
        "통계를 직접 인용해 보고서 본문에서 풀어낼 것. n_amplifications 는 REPOST "
        "건수와 같은 값(본문 없는 증폭)이며 QUOTE_POST 와 구분한다."
    )
    lines.append("```json")
    lines.append(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))
    lines.append("```")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["ComposedReport", "ReportComposer"]
