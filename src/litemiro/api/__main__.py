"""``litemiro-api`` CLI 진입점 — uvicorn 으로 FastAPI 앱을 띄운다.

기동 시 ``LiteLLMClient`` + ``STEmbedder`` 를 한 번만 만들어 ``RealPlazaRunner``
에 주입한다. sentence-transformers 모델 로딩이 수 초 걸리는데 매 plaza 마다
새로 만들면 첫 라운드 응답이 늘어진다. ``--fake`` 플래그는 LLM 없이 API 만
띄울 때 사용 — 프론트 전반 흐름만 확인할 때 편하다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import find_dotenv, load_dotenv

from litemiro.api.app import create_app
from litemiro.api.composer import RealPlazaComposer
from litemiro.api.ontology_store import OntologyRunResult
from litemiro.api.runner import RealPlazaRunner
from litemiro.api.store import RunnerOutcome

if TYPE_CHECKING:
    from litemiro.api.ontology_store import OntologyRunner
    from litemiro.api.store import PlazaComposer, PlazaRunner, ProgressCallback
    from litemiro.phase1.models import Preset


async def _noop_runner(
    *,
    plaza_id: str,
    ontology_a_path: Path,
    ontology_b_path: Path,
    rounds: int,
    event_log_path: Path,
    checkpoint_dir: Path,
    on_progress: ProgressCallback,
) -> RunnerOutcome:
    """``--fake`` 모드용: 라운드만큼 잠깐 sleep 하고 진행률을 채운다.

    프론트 폴링/상태 머신을 검증할 때 LLM 키 없이도 닫히도록 둔다.
    """
    del plaza_id, ontology_a_path, ontology_b_path
    del event_log_path, checkpoint_dir
    for r in range(rounds):
        await asyncio.sleep(0)
        on_progress(rounds_done=r + 1)
    return RunnerOutcome()


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="litemiro-api", description="Litemiro HTTP API server")
    parser.add_argument("--host", default=os.environ.get("LITEMIRO_API_HOST", "127.0.0.1"))
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("LITEMIRO_API_PORT", "8765"))
    )
    parser.add_argument(
        "--cors-origin",
        action="append",
        default=None,
        help="허용 CORS origin (반복 가능). 기본: http://localhost:5173",
    )
    parser.add_argument(
        "--data-dir",
        type=Path,
        default=Path(os.environ.get("LITEMIRO_API_DATA_DIR", "./runs/api")),
        help="plaza 별 events.jsonl + checkpoints/ 가 쌓이는 루트 (기본: ./runs/api)",
    )
    parser.add_argument(
        "--fake",
        action="store_true",
        help="LLM 없이 더미 runner 로 기동 — 프론트 polling 검증용",
    )
    parser.add_argument(
        "--llm-model",
        default=os.environ.get("LITEMIRO_API_LLM_MODEL", "openrouter/qwen/qwen-plus"),
    )
    return parser.parse_args(argv)


def _build_real_runner_and_composer(*, llm_model: str) -> tuple[PlazaRunner, PlazaComposer]:
    """실 시뮬레이션 runner + LLM composer — LLM 키 없이는 만들지 말 것.

    embedder / LiteLLM client 로딩이 무거워 모듈 단위가 아닌 main() 안에서
    한 번만 만든다. composer 는 runner 와 같은 ``LiteLLMClient`` 인스턴스를
    공유해 OpenRouter 커넥션 풀을 재사용한다.
    """
    from litemiro.embedding.sentence_transformers import STEmbedder  # noqa: PLC0415
    from litemiro.llm.litellm_client import LiteLLMClient  # noqa: PLC0415

    llm_client = LiteLLMClient()
    runner = RealPlazaRunner(
        llm_client=llm_client,
        embedder=STEmbedder(),
        llm_model=llm_model,
    )
    composer = RealPlazaComposer(llm_client=llm_client)
    return runner, composer


def _build_real_ontology_runner(*, llm_model: str) -> OntologyRunner:
    """Phase 1 ``OntologyPipeline`` 을 감싸 ``OntologyStore`` 가 부르는 시그니처에
    맞춘 closure 를 만든다. ``litellm.acompletion`` 콜이 1건의 ontology 당 분 단위
    — fake runner 와 달리 OpenRouter 키가 반드시 필요하다.

    pipeline 은 ``output_dir / ontology_{a,b}_*.json`` 두 파일을 떨군 뒤
    ``(OntologyA, OntologyB)`` 를 돌려준다. runner 는 그 경로를 그대로
    ``OntologyRunResult`` 에 박아 store 의 row 에 반영된다.
    """
    from litemiro.cli.ontology import Phase1LiteLLMClient  # noqa: PLC0415
    from litemiro.phase1.pipeline import OntologyPipeline, PipelineConfig  # noqa: PLC0415

    llm = Phase1LiteLLMClient()

    async def _run(
        *,
        document_path: Path,
        requirement: str,
        preset: Preset,
        output_dir: Path,
    ) -> OntologyRunResult:
        # OntologyStore 가 미리 mkdir 해 두지만 한 번 더 보장 — pipeline 의
        # serializer 가 write 직전에 dir 존재를 기대한다. 동기 호출 한 줄이라
        # event-loop 블록 무시 가능.
        output_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        config = PipelineConfig(
            input_path=document_path,
            requirement=requirement,
            preset=preset,
            output_dir=output_dir,
            model=llm_model,
        )
        ontology_a, _ = await OntologyPipeline(config, llm).run()
        return OntologyRunResult(
            ontology_a_path=output_dir / "ontology_a_persona.json",
            ontology_b_path=output_dir / "ontology_b_memory.json",
            agent_count=ontology_a.agent_count,
        )

    return _run


def main(argv: list[str] | None = None) -> int:
    # `.env` 의 OPENROUTER_API_KEY 자동 로드. cli/run.py 와 같은 동작.
    load_dotenv(find_dotenv(usecwd=True))
    args = _parse_args(argv)
    origins = tuple(args.cors_origin) if args.cors_origin else ("http://localhost:5173",)

    runner: PlazaRunner
    composer: PlazaComposer | None
    ontology_runner: OntologyRunner | None
    if args.fake:
        # --fake 는 LLM 키 없이도 닫혀야 한다 — composer/ontology_runner 도 함께
        # 비운다. POST /api/ontologies 는 503 으로 응답해 프론트가 /documents 만
        # 단독으로 검증할 수 있도록.
        runner = _noop_runner
        composer = None
        ontology_runner = None
    else:
        if not os.environ.get("OPENROUTER_API_KEY"):
            print(
                "Error: OPENROUTER_API_KEY is not set. Use --fake to start without an LLM.",
                file=sys.stderr,
            )
            return 1
        runner, composer = _build_real_runner_and_composer(llm_model=args.llm_model)
        ontology_runner = _build_real_ontology_runner(llm_model=args.llm_model)

    # uvicorn 은 ``[api]`` extra 에서만 들어오므로 main 안에서 import — fastapi
    # 만 깔린 테스트 환경에서도 모듈 import 가 깨지지 않도록.
    import uvicorn  # noqa: PLC0415

    app = create_app(
        runner=runner,
        base_dir=args.data_dir,
        composer=composer,
        ontology_runner=ontology_runner,
        cors_origins=origins,
    )
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
