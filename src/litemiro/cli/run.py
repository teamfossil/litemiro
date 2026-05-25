"""``litemiro-run`` — Phase 2 시뮬레이션 CLI 진입점.

``run_simulation`` (`integration/run.py`) 위의 얇은 argparse + 실 sentence-
transformers + LiteLLM wiring. issue #53.

Topic vocabulary 는 OntologyA 의 모든 ``AgentProfile.topics`` 의 union 을 정렬해
사용한다 — Phase 1 산출이 스스로 declare 한 토픽 어휘를 그대로 가져오므로 별도
큐레이션 불필요. ``topic_hierarchy`` 는 MVP 미사용 (contract Section 1).
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from litemiro.embedding.sentence_transformers import STEmbedder
from litemiro.integration.ontology_loader import OntologyLoader
from litemiro.integration.run import run_simulation
from litemiro.llm.litellm_client import LiteLLMClient

if TYPE_CHECKING:
    from collections.abc import Sequence

    from litemiro.core._types import SimulationResult
    from litemiro.interfaces import EmbedderLike, LLMClient


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="litemiro-run",
        description="Run the Phase 2 simulation from Phase 1 ontologies.",
    )
    parser.add_argument("--ontology-a", required=True, type=Path, help="Path to OntologyA JSON")
    parser.add_argument("--ontology-b", required=True, type=Path, help="Path to OntologyB JSON")
    parser.add_argument("--rounds", type=int, default=15, help="Total simulation rounds")
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
    parser.add_argument("--semaphore-limit", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--cooldown-seconds", type=float, default=0.5)
    return parser


def _topic_vocabulary(*, ontology_a_path: Path, ontology_b_path: Path) -> tuple[str, ...]:
    """OntologyA 의 모든 agent topics 의 union, 정렬.

    TopicExtractor 가 vocabulary 의 결정적 순서를 요구하지는 않지만, 같은 입력
    이 같은 vocab 을 만들어내야 결정성 테스트가 깨끗하다.
    """
    ontology_a, _ = OntologyLoader.load(
        ontology_a_path=ontology_a_path,
        ontology_b_path=ontology_b_path,
    )
    vocab: set[str] = set()
    for profile in ontology_a.agents.values():
        vocab.update(profile.topics)
    return tuple(sorted(vocab))


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
    vocabulary: Sequence[str],
) -> SimulationResult:
    """argparse 결과 + 의존성 → ``run_simulation`` 호출. 테스트가 직접 부르는
    경계로 두어 monkeypatch 없이 fake 주입이 가능하게 한다."""
    output_dir: Path = args.output_dir
    return await run_simulation(
        ontology_a_path=args.ontology_a,
        ontology_b_path=args.ontology_b,
        llm_client=llm_client,
        embedder=embedder,
        topic_vocabulary=vocabulary,
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
    try:
        vocabulary = _topic_vocabulary(
            ontology_a_path=args.ontology_a,
            ontology_b_path=args.ontology_b,
        )
        llm_client = LiteLLMClient()
        embedder = STEmbedder()
        result = asyncio.run(
            _run(args, llm_client=llm_client, embedder=embedder, vocabulary=vocabulary)
        )
    except Exception as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    _print_result(result)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
