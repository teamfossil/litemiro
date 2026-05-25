"""`ReportComposer` — `PartialInsights` → Markdown 보고서.

Phase 3 메모리 노트의 모델 라우팅: Claude Opus 가 1 차, 실패하면
Qwen-plus 가 폴백한다. 폴백 사용 여부는 ``ComposedReport.fallback_used``
로 노출해 비용 회계에 사용한다. 폴백이 또 실패하면 그대로 전파한다.

LLM 출력은 그대로 본문이 된다 — 후처리 / PDF 변환은 후속 이슈.
"""

from __future__ import annotations

import structlog
from pydantic import BaseModel, ConfigDict, Field

from litemiro.interfaces import LLMClient
from litemiro.phase3.models import (
    AggregationResult,
    PartialInsights,
    ReportConfig,
)

_logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "당신은 시뮬레이션 결과를 정리해 보고서를 작성하는 어시스턴트다. "
    "Markdown 으로 작성하라 — 제목은 `#`, 섹션은 `##`. "
    "주어진 통계와 카테고리별 인사이트만을 근거로 한다. "
    "한국어. 짧고 단정한 문장."
)


class ComposedReport(BaseModel):
    """`ReportComposer.compose` 결과 — 본문 + 회계."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    markdown: str
    model: str
    fallback_used: bool = False
    tokens_used: int = Field(default=0, ge=0)


class ReportComposer:
    def __init__(self, *, llm: LLMClient) -> None:
        self._llm = llm

    async def compose(
        self,
        *,
        result: AggregationResult,
        insights: PartialInsights,
        config: ReportConfig,
    ) -> ComposedReport:
        user = _build_user_prompt(result, insights)
        try:
            response = await self._llm.complete(
                system=_SYSTEM_PROMPT,
                user=user,
                model=config.composer_primary_model,
            )
        except Exception as exc:
            _logger.warning(
                "report_composer_primary_failed",
                primary_model=config.composer_primary_model,
                fallback_model=config.composer_fallback_model,
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


def _build_user_prompt(result: AggregationResult, insights: PartialInsights) -> str:
    lines = [
        "다음 시뮬레이션 결과를 Markdown 보고서로 정리하라.",
        "",
        "## 규모",
        f"- 이벤트 수: {result.n_events}",
        f"- 에이전트 수: {result.n_agents}",
        f"- 라운드 수: {result.n_rounds}",
        "",
        "## 카테고리별 인사이트",
    ]
    for item in insights.items:
        lines.append(f"### {item.category}")
        lines.append(item.summary)
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


__all__ = ["ComposedReport", "ReportComposer"]
