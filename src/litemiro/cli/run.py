"""``litemiro-run`` — Phase 2 시뮬레이션 CLI 진입점.

``run_simulation`` (`integration/run.py`) 위의 얇은 argparse + 실 sentence-
transformers + LiteLLM wiring. issue #53.

결정성 seed 는 ``OntologyA.seed`` 가 단독 소스 — CLI 가 ``--seed`` 를 따로
받지 않는다 (Phase 1 산출이 이미 declare 한 값). Topic vocabulary 역시
``run_simulation`` 이 ``OntologyA`` 에서 자동 도출하므로 본 CLI 는 인자만
파싱하고 실 의존 (LLM / Embedder) 만 인스턴스화해 넘긴다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

from litemiro.embedding.sentence_transformers import STEmbedder
from litemiro.integration.run import run_simulation
from litemiro.llm.litellm_client import LiteLLMClient

if TYPE_CHECKING:
    from litemiro.core._types import SimulationResult
    from litemiro.interfaces import EmbedderLike, LLMClient

log = structlog.get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litemiro-run",
        description="Run the Phase 2 simulation from Phase 1 ontologies.",
    )
    parser.add_argument("--ontology-a", required=True, type=Path, help="Path to OntologyA JSON")
    parser.add_argument("--ontology-b", required=True, type=Path, help="Path to OntologyB JSON")
    parser.add_argument(
        "--rounds", type=int, default=15, help="Total simulation rounds (default: 15)"
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("."),
        help="Output dir for events.jsonl + checkpoints/ (default: current dir)",
    )
    parser.add_argument(
        "--llm-model",
        default="openrouter/qwen/qwen-plus",
        help="LLM model identifier (default: openrouter/qwen/qwen-plus)",
    )
    parser.add_argument(
        "--token-budget",
        type=int,
        default=1_000_000,
        help="Per-simulation token cap (default: 1_000_000)",
    )
    parser.add_argument(
        "--semaphore-limit",
        type=int,
        default=10,
        help="Max concurrent LLM calls per batch (default: 10)",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=20,
        help="Active agents per concurrency batch (default: 20)",
    )
    parser.add_argument(
        "--cooldown-seconds",
        type=float,
        default=0.5,
        help="Sleep between batches to ease rate limits (default: 0.5)",
    )
    return parser


def _print_result(result: SimulationResult) -> None:
    print(f"Rounds run     : {result.rounds_run}")
    print(f"Early exit     : {result.early_exit}")
    print(f"Tokens used    : {result.tokens_used}")
    print(f"Event log      : {result.event_log_path}")
    print(f"Checkpoint dir : {result.checkpoint_dir}")


async def _run(
    args: argparse.Namespace,
    *,
    llm_client: LLMClient,
    embedder: EmbedderLike,
) -> SimulationResult:
    """argparse 결과 + 의존성 → ``run_simulation`` 호출. 테스트가 직접 부르는
    경계로 두어 의존성 주입이 깨끗하다 — main 의 `LiteLLMClient` / `STEmbedder`
    인스턴스화 단계를 우회해 fake 로 닫는다."""
    output_dir: Path = args.output_dir
    return await run_simulation(
        ontology_a_path=args.ontology_a,
        ontology_b_path=args.ontology_b,
        llm_client=llm_client,
        embedder=embedder,
        rounds=args.rounds,
        event_log_path=output_dir / "events.jsonl",
        checkpoint_dir=output_dir / "checkpoints",
        llm_model=args.llm_model,
        token_budget=args.token_budget,
        semaphore_limit=args.semaphore_limit,
        batch_size=args.batch_size,
        cooldown_seconds=args.cooldown_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if not os.environ.get("OPENROUTER_API_KEY"):
        # Pre-flight 체크: LiteLLM 호출 시점까지 가지 않고 즉시 실패.
        # 빠진 키로 OntologyLoader.load + StateStore 세팅 같은 비용을 치른 뒤
        # 첫 라운드의 LLM call 에서 실패하는 것보다 사용자 피드백이 명확하다.
        print("Error: OPENROUTER_API_KEY is not set", file=sys.stderr)
        return 1
    try:
        llm_client = LiteLLMClient()
        embedder = STEmbedder()
        result = asyncio.run(_run(args, llm_client=llm_client, embedder=embedder))
    except Exception as exc:
        log.error("litemiro_run.failed", error=str(exc))
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _print_result(result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
