"""``litemiro-api`` CLI 진입점 — uvicorn 으로 FastAPI 앱을 띄운다.

기동 시 ``LiteLLMClient`` + ``STEmbedder`` 를 한 번만 만들어 ``RealPlazaRunner``
에 주입한다. sentence-transformers 모델 로딩이 수 초 걸리는데 매 plaza 마다
새로 만들면 첫 라운드 응답이 늘어진다. ``--fake`` 플래그는 LLM 없이 API 만
띄울 때 사용 — 프론트 Seed → Ontology → Casting → Report 전 흐름을 LLM 키
없이 닫는다. fake 모드는 (1) dev fixture ontology 를 그대로 베껴 OntologyStore
에 흘리고 (2) 합성 events.jsonl 을 만들어 /report 가 0/0/0 으로 죽지 않게
하고 (3) stub markdown 한 장을 composer 대신 돌려준다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from dotenv import find_dotenv, load_dotenv

from litemiro.api.app import create_app
from litemiro.api.composer import ComposerOutcome, RealPlazaComposer
from litemiro.api.ontology_store import OntologyRunResult
from litemiro.api.runner import RealPlazaRunner
from litemiro.api.sample_fixtures import DEFAULT_ONTOLOGY_A_PATH, DEFAULT_ONTOLOGY_B_PATH
from litemiro.api.store import RunnerOutcome
from litemiro.models import Action, ActionType, RoundEvent

if TYPE_CHECKING:
    from litemiro.api.ontology_store import OntologyRunner
    from litemiro.api.store import PlazaComposer, PlazaRunner, ProgressCallback
    from litemiro.phase1.models import Preset


# 한 라운드 안에서 agent 인덱스를 회전시키면서 6 종을 골고루 섞는다. DO_NOTHING
# 도 포함 — /report 의 카테고리 분포가 비어 보이지 않도록.
_FAKE_ACTION_CYCLE: tuple[ActionType, ...] = (
    ActionType.CREATE_POST,
    ActionType.LIKE_POST,
    ActionType.REPOST,
    ActionType.QUOTE_POST,
    ActionType.FOLLOW,
    ActionType.DO_NOTHING,
)


def _build_fake_action(
    *, action_type: ActionType, round_num: int, agent_index: int, other_agent_id: str
) -> Action:
    """``ActionType`` 별 required 필드만 채운 결정적 합성 action.

    ``Action`` 의 model validator 가 type ↔ target/content 매칭을 강제 — 잘못
    채우면 검증 실패. round/index 만으로 재현 가능한 deterministic 값이라
    같은 plaza 를 두 번 만들어도 동일 jsonl 이 나온다.
    """
    if action_type == ActionType.CREATE_POST:
        return Action(type=action_type, content=f"fake post r={round_num} a={agent_index}")
    if action_type == ActionType.LIKE_POST:
        return Action(type=action_type, target_post_id=f"fake-post-r0-a{agent_index}")
    if action_type == ActionType.REPOST:
        return Action(type=action_type, target_post_id=f"fake-post-r0-a{agent_index}")
    if action_type == ActionType.QUOTE_POST:
        return Action(
            type=action_type,
            target_post_id=f"fake-post-r0-a{agent_index}",
            content=f"fake quote r={round_num} a={agent_index}",
        )
    if action_type == ActionType.FOLLOW:
        return Action(type=action_type, target_agent_id=other_agent_id)
    return Action(type=action_type)  # DO_NOTHING


def _write_fake_events(*, ontology_a_path: Path, rounds: int, event_log_path: Path) -> None:
    """OntologyA 의 agent_id 풀로 라운드 x agent 합성 events.jsonl 작성.

    ActionType 6 종을 (round + agent_index) % 6 으로 회전 → 카테고리 / follower
    flow / hot post 집계 모두 0 으로 떨어지지 않는다. /agents 와 같은 ontology
    fixture 에서 id 를 뽑으므로 캐스팅 100명 vs report 0명 같은 화면 간 충돌도
    같이 해소된다.
    """
    from litemiro.phase1.models import OntologyA  # noqa: PLC0415

    ontology_a = OntologyA.model_validate_json(ontology_a_path.read_text(encoding="utf-8"))
    agent_ids = list(ontology_a.agents.keys())
    if not agent_ids:
        return
    event_log_path.parent.mkdir(parents=True, exist_ok=True)
    now = datetime.now(UTC).replace(microsecond=0)
    with event_log_path.open("w", encoding="utf-8") as fh:
        for r in range(rounds):
            for i, agent_id in enumerate(agent_ids):
                action_type = _FAKE_ACTION_CYCLE[(r + i) % len(_FAKE_ACTION_CYCLE)]
                other = agent_ids[(i + 1) % len(agent_ids)]
                event = RoundEvent(
                    round_num=r,
                    timestamp=now,
                    agent_id=agent_id,
                    action=_build_fake_action(
                        action_type=action_type,
                        round_num=r,
                        agent_index=i,
                        other_agent_id=other,
                    ),
                )
                fh.write(event.to_jsonl() + "\n")


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
    """``--fake`` 모드용: 합성 events.jsonl 한 번 쓰고 진행률만 라운드 단위 폴링용으로 채운다.

    이전엔 events 작성 없이 sleep + on_progress 만 돌려 /agents·/layout 은 100명인데
    /report 는 0/0/0 으로 떨어졌었음. 프론트 화면 간 숫자가 충돌해 fake 의
    "프론트 전 흐름 검증" 가치가 없었던 게 동기.
    """
    del plaza_id, ontology_b_path, checkpoint_dir
    await asyncio.to_thread(
        _write_fake_events,
        ontology_a_path=ontology_a_path,
        rounds=rounds,
        event_log_path=event_log_path,
    )
    for r in range(rounds):
        await asyncio.sleep(0)
        on_progress(rounds_done=r + 1)
    return RunnerOutcome()


async def _noop_ontology_runner(
    *,
    document_path: Path,
    requirement: str,
    preset: Preset,
    output_dir: Path,
) -> OntologyRunResult:
    """``--fake`` 모드 Phase 1: dev fixture 두 ontology 를 그대로 복사해서 return.

    실 Phase 1 은 분 단위 LLM. fake 는 같은 fixture (``sample_quick_preset_
    ontology_*.json``) 를 결과 디렉터리로 복사해 OntologyStore 가 정상 completed
    로 인식하게 한다 — Seed 화면이 LLM 키 없이 닫혀 Casting 까지 이어진다.
    """
    from litemiro.phase1.models import OntologyA  # noqa: PLC0415

    del document_path, requirement, preset
    # 단일 동기 호출 — event-loop 블록 무시 가능. real ontology runner 와 같은 패턴.
    output_dir.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
    target_a = output_dir / "ontology_a_persona.json"
    target_b = output_dir / "ontology_b_memory.json"
    await asyncio.to_thread(shutil.copyfile, DEFAULT_ONTOLOGY_A_PATH, target_a)
    await asyncio.to_thread(shutil.copyfile, DEFAULT_ONTOLOGY_B_PATH, target_b)
    ontology_a = OntologyA.model_validate_json(target_a.read_text(encoding="utf-8"))
    return OntologyRunResult(
        ontology_a_path=target_a,
        ontology_b_path=target_b,
        agent_count=ontology_a.agent_count,
    )


async def _noop_composer(*, plaza_id: str, event_log_path: Path, preset: Preset) -> ComposerOutcome:
    """``--fake`` 보고서 합성: LLM 호출 없이 placeholder markdown + 합성 events 의 결정적 집계.

    events.jsonl 이 있으면 ``DataAggregator`` 만 돌려 ``aggregation`` 을 채운다
    (store 가 record 에 캐싱 → /report 가 매 호출마다 재집계 안 함). markdown 은
    fake 임을 알리는 한 줄짜리 placeholder. events 가 없으면 ``RealPlazaComposer``
    와 동일하게 ``markdown=None``.
    """
    from litemiro.phase3.data_aggregator import DataAggregator  # noqa: PLC0415

    del plaza_id, preset
    # ``RealPlazaComposer.__call__`` 와 같은 단일 stat — async to_thread 까지 갈
    # 가치 없음.
    if not event_log_path.exists():  # noqa: ASYNC240
        return ComposerOutcome(markdown=None)
    aggregation = DataAggregator.aggregate(event_log_path)
    markdown = (
        "# Fake plaza report\n\n"
        "이 광장은 `--fake` 모드로 생성된 더미입니다. "
        "실제 LLM 호출 없이 합성 events 로 채워졌습니다.\n"
    )
    return ComposerOutcome(markdown=markdown, aggregation=aggregation)


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
    # Phase 1 ontology generation 이 provider content filter (#121, Qwen 의
    # data_inspection_failed) 에 막혔을 때 자동 우회할 모델 리스트. 콤마 구분.
    # 정상 케이스는 primary 모델만 호출 — fallback 비용 영향 없음. 빈 문자열
    # 이면 fallback 비활성, default 는 OpenAI gpt-4o-mini 한 개.
    parser.add_argument(
        "--llm-fallback-models",
        default=os.environ.get("LITEMIRO_API_LLM_FALLBACK_MODELS", "openrouter/openai/gpt-4o-mini"),
        help="content filter 발동 시 순차 우회할 모델 (콤마 구분, 빈 문자열이면 비활성)",
    )
    return parser.parse_args(argv)


def _parse_fallback_models(raw: str) -> list[str]:
    return [m.strip() for m in raw.split(",") if m.strip()]


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


def _build_real_ontology_runner(
    *, llm_model: str, fallback_models: list[str] | None = None
) -> OntologyRunner:
    """Phase 1 ``OntologyPipeline`` 을 감싸 ``OntologyStore`` 가 부르는 시그니처에
    맞춘 closure 를 만든다. ``litellm.acompletion`` 콜이 1건의 ontology 당 분 단위
    — fake runner 와 달리 OpenRouter 키가 반드시 필요하다.

    pipeline 은 ``output_dir / ontology_{a,b}_*.json`` 두 파일을 떨군 뒤
    ``(OntologyA, OntologyB)`` 를 돌려준다. runner 는 그 경로를 그대로
    ``OntologyRunResult`` 에 박아 store 의 row 에 반영된다.

    ``fallback_models`` 가 비어있지 않으면 primary (``llm_model``) 가 provider
    content filter 에 막혔을 때 (``data_inspection_failed`` 등) 순차로 재시도.
    filter 외 에러는 즉시 전파 — rate-limit / network 은 fallback 으로 우회해도
    동일하게 실패할 가능성이 크다.
    """
    import logging  # noqa: PLC0415

    from litemiro.api.ontology_store import (  # noqa: PLC0415
        OntologyContentFilterBlockedError,
        is_content_filter_error,
    )
    from litemiro.cli.ontology import Phase1LiteLLMClient  # noqa: PLC0415
    from litemiro.phase1.pipeline import OntologyPipeline, PipelineConfig  # noqa: PLC0415

    log = logging.getLogger(__name__)
    chain = [llm_model, *(fallback_models or [])]
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
        last_filter_exc: Exception | None = None
        for idx, model in enumerate(chain):
            config = PipelineConfig(
                input_path=document_path,
                requirement=requirement,
                preset=preset,
                output_dir=output_dir,
                model=model,
            )
            try:
                ontology_a, _ = await OntologyPipeline(config, llm).run()
            except Exception as exc:
                if not is_content_filter_error(exc):
                    raise
                last_filter_exc = exc
                log.warning(
                    "ontology_content_filter_blocked",
                    extra={"model": model, "fallback_remaining": len(chain) - idx - 1},
                )
                continue
            return OntologyRunResult(
                ontology_a_path=output_dir / "ontology_a_persona.json",
                ontology_b_path=output_dir / "ontology_b_memory.json",
                agent_count=ontology_a.agent_count,
            )
        # primary + 모든 fallback 모델이 filter 에 막힘.
        raise OntologyContentFilterBlockedError(
            f"all models blocked by content filter: {chain}"
        ) from last_filter_exc

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
        # --fake 는 LLM 키 없이 Seed→Casting→Report 까지 닫는다 — runner/composer/
        # ontology_runner 셋 다 noop 으로 깐다. 셋 중 하나만 None 으로 두면 라우트
        # 미등록 (#1) 또는 빈 집계 (#2) 같은 비대칭이 다시 생긴다.
        runner = _noop_runner
        composer = _noop_composer
        ontology_runner = _noop_ontology_runner
    else:
        if not os.environ.get("OPENROUTER_API_KEY"):
            print(
                "Error: OPENROUTER_API_KEY is not set. Use --fake to start without an LLM.",
                file=sys.stderr,
            )
            return 1
        runner, composer = _build_real_runner_and_composer(llm_model=args.llm_model)
        ontology_runner = _build_real_ontology_runner(
            llm_model=args.llm_model,
            fallback_models=_parse_fallback_models(args.llm_fallback_models),
        )

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
