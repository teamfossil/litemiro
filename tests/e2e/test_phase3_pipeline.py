"""Phase 3 직렬 파이프라인 e2e.

DataAggregator → PatternAnalyzer → ReportComposer 까지 한 번에 굴려
``round_event_sample.jsonl`` 한 줄을 Markdown 본문으로 줄이는지 확인한다.

LLM 호출은 둘 다 ``_ScriptedLLM`` 으로 대체한다. 실제 OpenRouter 호출은
하지 않는다 (이슈 #26 스코프는 코드 경로 확인).
"""

from __future__ import annotations

from pathlib import Path

from litemiro.models import LLMResponse
from litemiro.phase1.models import Preset
from litemiro.phase3 import (
    DataAggregator,
    PatternAnalyzer,
    ReportComposer,
    ReportConfig,
)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SAMPLE_JSONL = _REPO_ROOT / "tests" / "data" / "round_event_sample.jsonl"


class _ScriptedLLM:
    """모델 ID 별 응답을 라운드 로빈으로 돌려준다."""

    def __init__(self, *, analyzer_summary: str, composer_markdown: str) -> None:
        self._analyzer_summary = analyzer_summary
        self._composer_markdown = composer_markdown
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
        self.calls.append((system, user, model))
        if model.startswith("openrouter/anthropic"):
            return LLMResponse(
                content=self._composer_markdown, prompt_tokens=200, completion_tokens=400
            )
        return LLMResponse(content=self._analyzer_summary, prompt_tokens=80, completion_tokens=30)


async def test_pipeline_produces_markdown_from_sample_jsonl() -> None:
    aggregated = DataAggregator.aggregate(_SAMPLE_JSONL)
    assert aggregated.n_events == 6

    llm = _ScriptedLLM(
        analyzer_summary="요약 한 줄.",
        composer_markdown="# Mirofish 시뮬레이션 보고서\n\n## 규모\n- 이벤트 6 건\n",
    )
    config = ReportConfig(
        preset=Preset.QUICK,
        analyzer_model="openrouter/qwen/qwen-plus",
        composer_primary_model="openrouter/anthropic/claude-opus-4.7",
        composer_fallback_model="openrouter/qwen/qwen-plus",
    )

    insights = await PatternAnalyzer(llm=llm).analyze(result=aggregated, config=config)
    assert len(insights.items) == 1
    assert insights.items[0].summary == "요약 한 줄."

    report = await ReportComposer(llm=llm).compose(
        result=aggregated, insights=insights, config=config
    )
    assert report.markdown.startswith("# Mirofish 시뮬레이션 보고서")
    assert report.fallback_used is False
    assert report.model == "openrouter/anthropic/claude-opus-4.7"

    # quick 은 분석 1 회 + 컴포저 1 회 = 총 2 회.
    assert len(llm.calls) == 2
    analyzer_models = {c[2] for c in llm.calls if "qwen" in c[2]}
    composer_models = {c[2] for c in llm.calls if "anthropic" in c[2]}
    assert analyzer_models == {"openrouter/qwen/qwen-plus"}
    assert composer_models == {"openrouter/anthropic/claude-opus-4.7"}


async def test_pipeline_falls_back_when_composer_primary_fails() -> None:
    """1 차 모델이 죽었을 때 보고서가 그래도 나오는지."""
    aggregated = DataAggregator.aggregate(_SAMPLE_JSONL)

    class _FailingPrimary:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str, str]] = []

        async def complete(self, *, system: str, user: str, model: str) -> LLMResponse:
            self.calls.append((system, user, model))
            if model.startswith("openrouter/anthropic"):
                raise RuntimeError("opus unreachable")
            return LLMResponse(content="요약.", prompt_tokens=40, completion_tokens=10)

    llm = _FailingPrimary()
    config = ReportConfig(
        preset=Preset.QUICK,
        analyzer_model="openrouter/qwen/qwen-plus",
        composer_primary_model="openrouter/anthropic/claude-opus-4.7",
        composer_fallback_model="openrouter/qwen/qwen-plus",
    )

    insights = await PatternAnalyzer(llm=llm).analyze(result=aggregated, config=config)
    report = await ReportComposer(llm=llm).compose(
        result=aggregated, insights=insights, config=config
    )

    assert report.fallback_used is True
    assert report.model == "openrouter/qwen/qwen-plus"
    # analyzer 1 + composer primary 시도 2 (재시도 1 회, PRD §4.3)
    # + composer fallback 1 = 4
    assert len(llm.calls) == 4
