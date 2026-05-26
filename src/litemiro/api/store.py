"""``PlazaStore`` — 메모리 상의 시뮬레이션 핸들 레지스트리.

step 1 은 의도적으로 in-memory. 프로세스가 죽으면 모든 plaza 가 사라진다.
영속화(SQLite/JSON)는 step 3 SSE 와 함께 다룬다.

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


class PlazaRunner(Protocol):
    """``PlazaStore.create`` 가 백그라운드 태스크로 실행하는 콜러블.

    실 구현(step 2+) 은 `litemiro.integration.run_simulation` 호출을 감싼다.
    테스트는 즉시 완료/실패/캔슬 시나리오를 흉내내는 fake 를 주입.

    구현체는 진행률을 반영하려면 ``on_progress(rounds_done)`` 를 호출해야 한다 —
    호출이 없으면 status 는 pending/running/completed 만 토글되고 round 카운트가
    멈춘 것처럼 보인다.
    """

    async def __call__(
        self,
        *,
        plaza_id: str,
        ontology_a_path: Path,
        ontology_b_path: Path,
        rounds: int,
        on_progress: ProgressCallback,
    ) -> None: ...


class ProgressCallback(Protocol):
    def __call__(self, *, rounds_done: int) -> None: ...


@dataclass
class PlazaRecord:
    plaza_id: str
    status: PlazaStatus
    rounds_total: int
    rounds_done: int = 0
    label: str | None = None
    error: str | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)


class PlazaStore:
    """단일 프로세스 내 plaza 라이프사이클 관리."""

    def __init__(self, runner: PlazaRunner) -> None:
        self._runner = runner
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
        record = PlazaRecord(
            plaza_id=plaza_id,
            status="pending",
            rounds_total=rounds,
            label=label,
        )
        async with self._lock:
            self._records[plaza_id] = record

        def on_progress(*, rounds_done: int) -> None:
            record.rounds_done = rounds_done

        async def _drive() -> None:
            record.status = "running"
            try:
                await self._runner(
                    plaza_id=plaza_id,
                    ontology_a_path=ontology_a_path,
                    ontology_b_path=ontology_b_path,
                    rounds=rounds,
                    on_progress=on_progress,
                )
            except Exception as exc:
                record.status = "failed"
                record.error = f"{type(exc).__name__}: {exc}"
                return
            record.status = "completed"
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


__all__ = ["PlazaRecord", "PlazaRunner", "PlazaStore", "ProgressCallback"]
