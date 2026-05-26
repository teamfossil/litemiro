"""``PlazaStore`` — 메모리 상의 시뮬레이션 핸들 레지스트리.

step 2 부터는 plaza 마다 디스크 디렉토리 (``base_dir/{plaza_id}/``) 가 생성되어
``events.jsonl`` + ``checkpoints/`` 가 저장된다. 메타데이터(상태, 경로, 토큰)
자체는 아직 in-memory — 영속화는 step 3 SSE 와 함께 본다.

테스트 격리를 위해 ``PlazaRunner`` Protocol 로 백엔드 호출을 추상화 —
실 구현은 `run_simulation` 을 호출, 테스트는 즉시 완료/실패하는 fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from litemiro.api.models import PlazaStatus


@dataclass
class RunnerOutcome:
    """``PlazaRunner.__call__`` 의 반환 — 결과 메트릭만 담는다."""

    tokens_used: int = 0


class ProgressCallback(Protocol):
    def __call__(self, *, rounds_done: int) -> None: ...


class PlazaRunner(Protocol):
    """``PlazaStore.create`` 가 백그라운드 태스크로 실행하는 콜러블.

    실 구현(step 2+) 은 `litemiro.integration.run_simulation` 호출을 감싼다.
    테스트는 즉시 완료/실패/캔슬 시나리오를 흉내내는 fake 를 주입.

    ``on_progress`` 가 호출되지 않으면 status 는 pending/running/completed 만
    토글되고 round 카운트가 멈춘 것처럼 보인다 — 호출자는 라운드 종료마다
    ``on_progress(rounds_done=...)`` 를 불러야 한다.
    """

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
    ) -> RunnerOutcome: ...


@dataclass
class PlazaRecord:
    plaza_id: str
    status: PlazaStatus
    rounds_total: int
    rounds_done: int = 0
    label: str | None = None
    error: str | None = None
    tokens_used: int = 0
    ontology_a_path: Path | None = None
    ontology_b_path: Path | None = None
    event_log_path: Path | None = None
    checkpoint_dir: Path | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class PlazaStore:
    """단일 프로세스 내 plaza 라이프사이클 관리.

    ``base_dir`` 아래에 plaza_id 별 서브디렉토리를 만들어 events.jsonl /
    checkpoints/ 를 둔다. 디렉토리가 존재하면 그대로 사용 (재시작 후 동일
    plaza_id 로 재현되는 경우 없음 — UUID 이라 충돌 사실상 0).
    """

    def __init__(self, *, runner: PlazaRunner, base_dir: Path) -> None:
        self._runner = runner
        self._base_dir = base_dir
        self._records: dict[str, PlazaRecord] = {}
        self._lock = asyncio.Lock()

    async def create(
        self,
        *,
        ontology_a_path: Path,
        ontology_b_path: Path,
        rounds: int,
        label: str | None,
    ) -> PlazaRecord:
        plaza_id = uuid.uuid4().hex
        plaza_root = self._base_dir / plaza_id
        plaza_root.mkdir(parents=True, exist_ok=True)
        event_log_path = plaza_root / "events.jsonl"
        checkpoint_dir = plaza_root / "checkpoints"
        checkpoint_dir.mkdir(exist_ok=True)
        record = PlazaRecord(
            plaza_id=plaza_id,
            status="pending",
            rounds_total=rounds,
            label=label,
            ontology_a_path=ontology_a_path,
            ontology_b_path=ontology_b_path,
            event_log_path=event_log_path,
            checkpoint_dir=checkpoint_dir,
        )
        async with self._lock:
            self._records[plaza_id] = record

        def on_progress(*, rounds_done: int) -> None:
            record.rounds_done = rounds_done

        async def _drive() -> None:
            record.status = "running"
            try:
                outcome = await self._runner(
                    plaza_id=plaza_id,
                    ontology_a_path=ontology_a_path,
                    ontology_b_path=ontology_b_path,
                    rounds=rounds,
                    event_log_path=event_log_path,
                    checkpoint_dir=checkpoint_dir,
                    on_progress=on_progress,
                )
            except Exception as exc:
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
                return
            record.status = "completed"
            record.tokens_used = outcome.tokens_used
            # runner 가 진행률을 보고하지 않았으면 종료 시점에 한 번 채워준다.
            record.rounds_done = max(record.rounds_done, rounds)

        record.task = asyncio.create_task(_drive(), name=f"plaza-{plaza_id}")
        return record

    async def get(self, plaza_id: str) -> PlazaRecord | None:
        async with self._lock:
            return self._records.get(plaza_id)

    async def shutdown(self) -> None:
        """프로세스 종료 시 미완료 태스크를 모두 취소한다.

        FastAPI lifespan 의 종료 단계에서 호출 — 테스트는 명시적으로 호출한다.
        """
        async with self._lock:
            tasks = [r.task for r in self._records.values() if r.task and not r.task.done()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t


__all__ = [
    "PlazaRecord",
    "PlazaRunner",
    "PlazaStore",
    "ProgressCallback",
    "RunnerOutcome",
]
