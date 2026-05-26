"""``RealPlazaRunner`` — HTTP API → ``run_simulation`` 어댑터.

CLI (``litemiro-run``) 가 매 호출마다 ``LiteLLMClient`` / ``STEmbedder`` 를
새로 만드는 것과 달리, API 는 프로세스 한 번에 둘을 공유한다. ``STEmbedder``
의 ``sentence-transformers`` 모델 로딩만 수 초가 걸려서, 매 plaza 마다
새로 만들면 첫 라운드 응답이 지연된다.

라운드 진행률은 ``EventLogger`` 가 라인 단위로 flush 하므로, ``run_simulation``
내부에서 직접 콜백을 꽂기보단 끝난 뒤 ``rounds_run`` 으로 일괄 보고한다 —
SSE 라이브 스트림(step 3) 이 들어오면 그때 라운드별 progress 가 의미를 갖는다.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from litemiro.api.store import RunnerOutcome
from litemiro.integration.run import run_simulation

if TYPE_CHECKING:
    from pathlib import Path

    from litemiro.api.store import ProgressCallback
    from litemiro.interfaces import EmbedderLike, LLMClient


class RealPlazaRunner:
    """``run_simulation`` 을 HTTP API 한 프로세스에서 재사용 가능한 형태로 감싼다.

    ``llm_client`` / ``embedder`` 는 ``litemiro-api`` 기동 시 한 번만 만들어
    모든 plaza 가 공유한다. 라운드 수와 토큰 예산은 plaza 단위로 받으므로
    생성자에서는 디폴트만 잡고 ``__call__`` 시 override 한다 (현 step 2 는
    POST body 에 token_budget 노출 안 함 → 디폴트 사용).
    """

    def __init__(
        self,
        *,
        llm_client: LLMClient,
        embedder: EmbedderLike,
        llm_model: str = "openrouter/qwen/qwen-plus",
        token_budget: int = 1_000_000,
        semaphore_limit: int = 10,
        batch_size: int = 20,
        cooldown_seconds: float = 0.5,
    ) -> None:
        self._llm_client = llm_client
        self._embedder = embedder
        self._llm_model = llm_model
        self._token_budget = token_budget
        self._semaphore_limit = semaphore_limit
        self._batch_size = batch_size
        self._cooldown_seconds = cooldown_seconds

    async def __call__(
        self,
        *,
        plaza_id: str,
        ontology_a_path: Path,
        ontology_b_path: Path,
        rounds: int,
        event_log_path: Path,
        checkpoint_dir: Path,
        on_progress: ProgressCallback,
    ) -> RunnerOutcome:
        del plaza_id  # 로깅용 키만 받아두고 run_simulation 자체는 경로로 분리
        result = await run_simulation(
            ontology_a_path=ontology_a_path,
            ontology_b_path=ontology_b_path,
            llm_client=self._llm_client,
            embedder=self._embedder,
            rounds=rounds,
            event_log_path=event_log_path,
            checkpoint_dir=checkpoint_dir,
            llm_model=self._llm_model,
            token_budget=self._token_budget,
            semaphore_limit=self._semaphore_limit,
            batch_size=self._batch_size,
            cooldown_seconds=self._cooldown_seconds,
        )
        # SSE 도입 전까지는 종료 시점에 한 번만 진행률을 채운다 — store 도 같은
        # 안전망(``max(rounds_done, rounds)``) 을 들고 있어서 중복 호출 OK.
        on_progress(rounds_done=result.rounds_run)
        return RunnerOutcome(tokens_used=result.tokens_used)


__all__ = ["RealPlazaRunner"]
