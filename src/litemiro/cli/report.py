"""``litemiro-report`` — Phase 3 보고서 CLI 진입점.

``events.jsonl`` (Phase 2 산출) → 4 카테고리 통계 (`DataAggregator`, LLM 무관)
→ 카테고리별 인사이트 (`PatternAnalyzer`) → Markdown 보고서 (`ReportComposer`).

``--preset`` 이 PatternAnalyzer 호출 수를 결정한다 (quick 1 / standard 4 / full 4).
Composer 는 Claude Opus 가 1 차, tenacity 재시도 1 회 후 Qwen-plus 폴백 — 폴백
사용 여부는 stdout 요약에 그대로 노출해 비용 회계가 가능하게 한다.

``--output`` 디폴트는 ``./reports/report-{ISO timestamp}.md`` 로 매 실행마다
새 파일을 만든다 — 같은 경로를 덮어써 이전 보고서를 잃는 트랩을 차단.

``OPENROUTER_API_KEY`` 는 ``.env`` 파일에서 자동 로드된다 (``python-dotenv``).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from dotenv import find_dotenv, load_dotenv

from litemiro.llm.litellm_client import LiteLLMClient
from litemiro.phase1.models import Preset
from litemiro.phase3.data_aggregator import DataAggregator
from litemiro.phase3.models import ReportConfig
from litemiro.phase3.pattern_analyzer import PatternAnalyzer
from litemiro.phase3.report_composer import ReportComposer

if TYPE_CHECKING:
    from litemiro.interfaces import LLMClient

log = structlog.get_logger(__name__)


@dataclass(frozen=True, slots=True)
class _ReportSummary:
    """``_run`` 의 반환값 — CLI 와 테스트가 공유하는 결과 표면.

    ``markdown`` 은 보고서 본문을 포함한다 — 디스크 쓰기는 main 의 동기
    단계 (``_write_output``) 에서 수행해 async 본체가 sync I/O 를 끌어안지
    않게 한다 (ASYNC240).
    """

    output_path: Path
    preset: Preset
    composer_model: str
    composer_fallback_used: bool
    analyzer_total_tokens: int
    composer_tokens: int
    markdown: str


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litemiro-report",
        description="Generate a Phase 3 Markdown report from a Phase 2 event log.",
    )
    parser.add_argument(
        "--events",
        required=True,
        type=Path,
        help="Path to events.jsonl produced by litemiro-run",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Markdown output path (default: ./reports/report-{ISO timestamp}.md — new file per run)"
        ),
    )
    parser.add_argument(
        "--preset",
        type=Preset,
        choices=list(Preset),
        default=Preset.QUICK,
        help="Analyzer call shape (quick=1 / standard=4 / full=4)",
    )
    parser.add_argument(
        "--analyzer-model",
        default="openrouter/qwen/qwen-plus",
        help="Analyzer LLM model identifier (default: openrouter/qwen/qwen-plus)",
    )
    parser.add_argument(
        "--composer-primary-model",
        default="openrouter/anthropic/claude-opus-4.7",
        help="Composer primary model (default: openrouter/anthropic/claude-opus-4.7)",
    )
    parser.add_argument(
        "--composer-fallback-model",
        default="openrouter/qwen/qwen-plus",
        help="Composer fallback model (default: openrouter/qwen/qwen-plus)",
    )
    return parser


def _default_output() -> Path:
    """``./reports/report-{ISO timestamp}.md`` — 매 실행마다 새 파일.

    timestamp 는 colon-free + 마이크로초 포함 — 같은 초에 두 번 호출돼도
    충돌하지 않고, Windows 파일명에서도 안전.
    """
    ts = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    return Path("reports") / f"report-{ts}.md"


async def _run(
    args: argparse.Namespace,
    *,
    llm_client: LLMClient,
) -> _ReportSummary:
    """argparse 결과 + LLM client → 보고서 파일 생성.

    테스트가 직접 부르는 경계로 두어 main 의 ``LiteLLMClient`` 인스턴스화
    단계를 우회한다 — 실 OpenRouter 키 없이도 fake LLM 으로 닫힌다.
    """
    aggregation = DataAggregator.aggregate(args.events)
    config = ReportConfig(
        preset=args.preset,
        analyzer_model=args.analyzer_model,
        composer_primary_model=args.composer_primary_model,
        composer_fallback_model=args.composer_fallback_model,
    )
    insights = await PatternAnalyzer(llm=llm_client).analyze(result=aggregation, config=config)
    report = await ReportComposer(llm=llm_client).compose(
        result=aggregation, insights=insights, config=config
    )
    output_path: Path = args.output if args.output is not None else _default_output()
    return _ReportSummary(
        output_path=output_path,
        preset=args.preset,
        composer_model=report.model,
        composer_fallback_used=report.fallback_used,
        analyzer_total_tokens=sum(item.tokens_used for item in insights.items),
        composer_tokens=report.tokens_used,
        markdown=report.markdown,
    )


def _write_output(summary: _ReportSummary) -> None:
    """``_run`` 산출을 디스크에 쓴다 — 동기 I/O 는 main 단에서만 수행."""
    summary.output_path.parent.mkdir(parents=True, exist_ok=True)
    summary.output_path.write_text(summary.markdown, encoding="utf-8")


def _print_summary(summary: _ReportSummary) -> None:
    print(f"Preset            : {summary.preset.value}")
    print(f"Output            : {summary.output_path}")
    print(f"Composer model    : {summary.composer_model}")
    print(f"Composer fallback : {summary.composer_fallback_used}")
    print(f"Analyzer tokens   : {summary.analyzer_total_tokens}")
    print(f"Composer tokens   : {summary.composer_tokens}")


def main(argv: list[str] | None = None) -> int:
    # ``find_dotenv(usecwd=True)`` 가 사용자 cwd 기준 ``.env`` 를 찾는다 —
    # 디폴트 (caller 파일 기준) 면 패키지 루트의 ``.env`` 가 우선되어 사용자
    # 작업 디렉토리에 둔 ``.env`` 를 못 찾는 트랩을 막는다.
    load_dotenv(find_dotenv(usecwd=True))
    args = _build_parser().parse_args(argv)
    if not os.environ.get("OPENROUTER_API_KEY"):
        # Pre-flight 체크: 첫 LLM 호출에서 죽기 전에 즉시 명시적으로 실패해야
        # 사용자 피드백이 분명하다 (Aggregator 만 돌리고 polluted 보고서가
        # 떨어지는 일도 막는다).
        print("Error: OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1
    try:
        llm_client = LiteLLMClient()
        summary = asyncio.run(_run(args, llm_client=llm_client))
        _write_output(summary)
    except Exception as exc:
        log.error("litemiro_report.failed", error=str(exc))
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _print_summary(summary)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
