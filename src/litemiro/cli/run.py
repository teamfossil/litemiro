"""``litemiro-run`` — Phase 2 시뮬레이션 CLI 진입점.

``run_simulation`` (`integration/run.py`) 위의 얇은 argparse + 실 sentence-
transformers + LiteLLM wiring. issue #53.

결정성 seed 는 ``OntologyA.seed`` 가 단독 소스 — CLI 가 ``--seed`` 를 따로
받지 않는다 (Phase 1 산출이 이미 declare 한 값). Topic vocabulary 역시
``run_simulation`` 이 ``OntologyA`` 에서 자동 도출하므로 본 CLI 는 인자만
파싱하고 실 의존 (LLM / Embedder) 만 인스턴스화해 넘긴다.

``--output-dir`` 디폴트는 ``./runs/run-{ISO timestamp}/`` 로 매 실행마다 새
디렉토리를 만든다 — ``EventLogger`` 가 append 모드라 같은 디렉토리를 재사용
하면 JSONL 이 누적되어 Phase 3 분석이 오염되는 트랩을 원천 차단. 디스크
누적은 사용자 책임 (운영에서는 명시적인 경로 지정 권장).

``OPENROUTER_API_KEY`` 는 ``.env`` 파일에서 자동 로드된다 (``python-dotenv``).
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import structlog
from dotenv import find_dotenv, load_dotenv

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
        default=None,
        help=(
            "Output dir for events.jsonl + checkpoints/ "
            "(default: ./runs/run-{ISO timestamp}/ — new directory per run "
            "to avoid JSONL append accumulation)"
        ),
    )
    parser.add_argument(
        "--llm-model",
        default="openrouter/qwen/qwen-plus",
        help="LLM model identifier (default: openrouter/qwen/qwen-plus)",
    )
    parser.add_argument(
        "--token-budget",
        type=int,
        default=3_000_000,
        help="Per-simulation token cap (default: 3_000_000)",
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
    parser.add_argument(
        "--reuse-output-dir",
        action="store_true",
        help=(
            "Reuse an --output-dir that already has events.jsonl or checkpoints/. "
            "Off by default to prevent Phase 3 double-counting and resume from "
            "stale checkpoints."
        ),
    )
    return parser


def _print_result(result: SimulationResult) -> None:
    print(f"Rounds run     : {result.rounds_run}")
    print(f"Early exit     : {result.early_exit}")
    print(f"Tokens used    : {result.tokens_used}")
    print(f"Event log      : {result.event_log_path}")
    print(f"Checkpoint dir : {result.checkpoint_dir}")


def _default_output_dir() -> Path:
    """``./runs/run-{ISO timestamp}/`` — 매 실행마다 새 디렉토리.

    timestamp 는 UTC + colon-free 포맷 (Windows 파일명 안전). 마이크로초까지
    포함해 같은 초에 두 번 호출돼도 충돌하지 않게 한다.
    """
    ts = datetime.now().strftime("%Y%m%dT%H%M%S%f")
    return Path("runs") / f"run-{ts}"


async def _run(
    args: argparse.Namespace,
    *,
    llm_client: LLMClient,
    embedder: EmbedderLike,
) -> SimulationResult:
    """argparse 결과 + 의존성 → ``run_simulation`` 호출. 테스트가 직접 부르는
    경계로 두어 의존성 주입이 깨끗하다 — main 의 `LiteLLMClient` / `STEmbedder`
    인스턴스화 단계를 우회해 fake 로 닫는다."""
    output_dir: Path = args.output_dir if args.output_dir is not None else _default_output_dir()
    event_log_path = output_dir / "events.jsonl"
    checkpoint_dir = output_dir / "checkpoints"
    # ``--output-dir`` 재사용 footgun. EventLogger 는 append 모드라 events.jsonl 잔재가
    # 그대로 누적돼 Phase 3 집계가 오염되고 (관측: 같은 (round, agent) 쌍이 2~3회
    # 등장 → 액션 분포·posts_created 왜곡), checkpoints/ 잔재는 향후 resume 경로에서
    # stale state 위에 새 events 가 얹혀 더 미묘한 mismatch 를 만든다. ``--reuse-output-dir``
    # 없이는 둘 다 사전 검사로 abort.
    if not args.reuse_output_dir:
        if event_log_path.exists() and event_log_path.stat().st_size > 0:
            raise FileExistsError(
                f"{event_log_path} already exists. Reusing it would double-count "
                "events into Phase 3 aggregates. Delete it, pass a fresh --output-dir, "
                "or use --reuse-output-dir to intentionally append."
            )
        if checkpoint_dir.exists() and any(checkpoint_dir.iterdir()):
            raise FileExistsError(
                f"{checkpoint_dir} contains stale checkpoint files that could "
                "resume into a fresh run. Delete it, pass a fresh --output-dir, "
                "or use --reuse-output-dir to intentionally reuse."
            )
    return await run_simulation(
        ontology_a_path=args.ontology_a,
        ontology_b_path=args.ontology_b,
        llm_client=llm_client,
        embedder=embedder,
        rounds=args.rounds,
        event_log_path=event_log_path,
        checkpoint_dir=checkpoint_dir,
        llm_model=args.llm_model,
        token_budget=args.token_budget,
        semaphore_limit=args.semaphore_limit,
        batch_size=args.batch_size,
        cooldown_seconds=args.cooldown_seconds,
    )


def main(argv: list[str] | None = None) -> int:
    # `.env` 의 OPENROUTER_API_KEY 등을 환경변수로 자동 로드. 셸 export 가
    # 이미 있으면 그쪽이 우선 — `.env` 가 production 환경을 덮어쓰지 않음.
    # ``find_dotenv`` 의 ``usecwd=True`` 가 사용자 cwd 기준 검색을 보장 —
    # 디폴트 (caller 파일 기준) 면 프로젝트 루트의 ``.env`` 가 우선되어
    # 사용자가 자신의 작업 디렉토리에 둔 ``.env`` 를 못 찾는다.
    load_dotenv(find_dotenv(usecwd=True))
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
