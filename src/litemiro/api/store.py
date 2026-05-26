"""``PlazaStore`` — 시뮬레이션 핸들 레지스트리.

step 2 부터는 plaza 마다 디스크 디렉토리 (``base_dir/{plaza_id}/``) 가 생성되어
``events.jsonl`` + ``checkpoints/`` 가 저장된다. 메타데이터(상태, 경로, 토큰,
markdown) 는 ``base_dir/plazas.db`` (SQLite) 에 영속 — 프로세스 재시작 후에도
``GET /plazas/{id}/status`` / ``/report`` 가 404 가 아니라 디스크 산출물을 다시
바라본다. 자세한 컬럼/규칙은 ``api/db.py``.

테스트 격리를 위해 ``PlazaRunner`` Protocol 로 백엔드 호출을 추상화 —
실 구현은 `run_simulation` 을 호출, 테스트는 즉시 완료/실패하는 fake.
"""

from __future__ import annotations

import asyncio
import contextlib
import sqlite3
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, Protocol

from litemiro.api import db as _db
from litemiro.api.composer import ComposerOutcome
from litemiro.api.models import PlazaStatus
from litemiro.phase1.models import Preset
from litemiro.phase3.models import AggregationResult

# SSE 이벤트의 두 가지 분류 —
#  * progress: 라운드 진행률 갱신 (rounds_done 증가)
#  * status:   상태 머신 전환 (running/completed/failed). status="completed"|"failed"
#              가 들어오면 스트림 종료 신호로도 같이 쓰인다.
EventType = Literal["progress", "status"]


@dataclass
class RunnerOutcome:
    """``PlazaRunner.__call__`` 의 반환 — 결과 메트릭만 담는다.

    ``rounds_run`` 은 runner 가 실제로 돈 라운드 수. ``None`` 이면 store 는
    ``on_progress`` 가 마지막으로 보고한 값을 그대로 둔다. 토큰 예산 소진처럼
    early-exit 으로 ``rounds_run < rounds`` 인 경우를 표현하기 위해 도입.
    """

    tokens_used: int = 0
    rounds_run: int | None = None


class ProgressCallback(Protocol):
    def __call__(self, *, rounds_done: int) -> None: ...


@dataclass(frozen=True)
class PlazaEvent:
    """SSE 스트림으로 흘려보낼 단일 이벤트.

    ``data`` 는 SSE wire 포맷에서 JSON 으로 직렬화되므로 JSON-safe 한 dict.
    상태 머신 전환에서 ``data["status"]`` 가 terminal 값이면 라우트는 이
    이벤트를 마지막으로 스트림을 닫는다.
    """

    type: EventType
    data: dict[str, Any]


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


class PlazaComposer(Protocol):
    """sim 완료 직후 store 가 호출하는 LLM 보고서 어댑터 (step 4).

    실 구현(`RealPlazaComposer`) 은 PatternAnalyzer + ReportComposer 를 묶고,
    테스트/--fake 는 즉시 stub markdown 또는 ``markdown=None`` 을 돌려준다.
    실패 (Opus+Qwen 동시 사망) 도 예외가 아니라 ``markdown=None`` outcome 으로
    표현해 plaza 상태 머신을 깨지 않는다 — sim 은 성공했는데 LLM 만 죽은 경우
    status=failed 로 떨어뜨리면 통계 보고서까지 못 보게 되니까.

    ``preset`` 은 plaza 단위로 호출자가 정한다 (CreatePlazaRequest.preset) —
    같은 composer 가 여러 plaza 의 quick/standard/full 을 처리한다.
    """

    async def __call__(
        self,
        *,
        plaza_id: str,
        event_log_path: Path,
        preset: Preset,
    ) -> ComposerOutcome: ...


@dataclass
class PlazaRecord:
    plaza_id: str
    status: PlazaStatus
    rounds_total: int
    rounds_done: int = 0
    label: str | None = None
    error: str | None = None
    tokens_used: int = 0
    # 보고서 합성 단계의 호출 수 / 청킹 결정 — 시뮬레이션 자체와는 직교.
    preset: Preset = Preset.QUICK
    ontology_a_path: Path | None = None
    ontology_b_path: Path | None = None
    event_log_path: Path | None = None
    checkpoint_dir: Path | None = None
    task: asyncio.Task[None] | None = field(default=None, repr=False)
    # SSE 구독자별 큐. ``PlazaStore.subscribe`` 가 큐를 만들어 여기에 등록하고,
    # 라우트가 종료/disconnect 시 ``unsubscribe`` 로 떼어낸다. 큐는 unbounded —
    # producer 가 라운드 단위(LLM 호출 사이) 라 사실상 빠르지 않다.
    subscribers: list[asyncio.Queue[PlazaEvent]] = field(default_factory=list, repr=False)
    # step 4 — LLM ReportComposer 가 채우는 Markdown 본문. compose 가 아직
    # 안 돌았거나 Opus+Qwen 동시 사망으로 폴백된 경우 ``None``. /report 응답이
    # 그대로 노출.
    report_markdown: str | None = None
    report_fallback_used: bool = False
    # ``DataAggregator.aggregate`` 결과 캐시. composer 가 한 번 돌면 outcome 을
    # 통해 채워지고, ``/report`` 가 매 호출마다 events.jsonl 을 재집계하지 않는다.
    # composer 가 없는 fake 경로는 ``build_report`` 가 lazy 로 채운다.
    aggregation_cache: AggregationResult | None = field(default=None, repr=False)


class PlazaStore:
    """단일 프로세스 내 plaza 라이프사이클 관리.

    ``base_dir`` 아래에 plaza_id 별 서브디렉토리를 만들어 events.jsonl /
    checkpoints/ 를 둔다. 디렉토리가 존재하면 그대로 사용 (재시작 후 동일
    plaza_id 로 재현되는 경우 없음 — UUID 이라 충돌 사실상 0).
    """

    def __init__(
        self,
        *,
        runner: PlazaRunner,
        base_dir: Path,
        composer: PlazaComposer | None = None,
        db_path: Path | None = None,
    ) -> None:
        self._runner = runner
        self._composer = composer
        self._base_dir = base_dir
        self._records: dict[str, PlazaRecord] = {}
        # 단일 이벤트 루프 가정 하에 ``_records`` dict 구조 변경만 보호한다.
        # record 필드 (status/rounds_done) 와 subscribers 리스트 변경은
        # CPython 단일 루프 atomicity 에 의존 — SSE pub/sub 도 같은 모델.
        self._lock = asyncio.Lock()
        # ``db_path=None`` 은 비영속 모드 — 단위 테스트에서 격리 위해 유지.
        # 경로가 주어지면 즉시 hydrate 해서 ``_records`` 를 채운다 — running/
        # composing/pending 으로 마지막 commit 된 row 는 ``failed`` 로 강제
        # 마킹돼 들어온다 (``db.load_all`` 참조).
        self._db: sqlite3.Connection | None = None
        if db_path is not None:
            self._db = _db.connect(db_path)
            for record in _db.load_all(self._db):
                self._records[record.plaza_id] = record

    @staticmethod
    def _broadcast(record: PlazaRecord, event: PlazaEvent) -> None:
        """모든 subscriber 큐에 이벤트를 push.

        라우트가 종료되기 전에 disconnect 한 경우 큐는 unsubscribe 로 빠지지만,
        그 사이 짧은 race 로 dead 큐가 남을 수 있다 → ``put_nowait`` 가 unbounded
        에서는 실패 안 함. snapshot 으로 iterate 해서 중간 unsubscribe 와 안전.
        """
        for queue in list(record.subscribers):
            queue.put_nowait(event)

    def _persist(self, record: PlazaRecord) -> None:
        """현재 record 스냅샷을 SQLite 로 흘려보낸다 (db_path=None 이면 no-op).

        모든 mutation 직후 호출 — 라운드 단위 progress 까지 영속한다. sqlite
        write 는 WAL + ``synchronous=NORMAL`` 로 라운드 한 번당 < 1ms 가정 —
        라운드 wall-clock (수 초) 에 비해 무시 가능. 같은 단일 이벤트 루프
        스레드에서 호출되므로 ``check_same_thread=False`` 와도 별개로 안전.
        """
        if self._db is None:
            return
        _db.upsert_record(self._db, record)

    async def create(
        self,
        *,
        ontology_a_path: Path,
        ontology_b_path: Path,
        rounds: int,
        label: str | None,
        preset: Preset = Preset.QUICK,
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
            preset=preset,
            ontology_a_path=ontology_a_path,
            ontology_b_path=ontology_b_path,
            event_log_path=event_log_path,
            checkpoint_dir=checkpoint_dir,
        )
        async with self._lock:
            self._records[plaza_id] = record
            self._persist(record)

        def on_progress(*, rounds_done: int) -> None:
            record.rounds_done = rounds_done
            self._broadcast(
                record,
                PlazaEvent(
                    type="progress",
                    data={"rounds_done": rounds_done, "rounds_total": rounds},
                ),
            )
            self._persist(record)

        def _emit_status() -> None:
            self._broadcast(
                record,
                PlazaEvent(
                    type="status",
                    data={
                        "status": record.status,
                        "rounds_done": record.rounds_done,
                        "rounds_total": record.rounds_total,
                        "error": record.error,
                    },
                ),
            )

        async def _drive() -> None:
            record.status = "running"
            self._persist(record)
            _emit_status()
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
                self._persist(record)
                _emit_status()
                return
            record.tokens_used = outcome.tokens_used
            # outcome.rounds_run 이 있으면 그걸 신뢰 (early-exit 인 경우
            # ``rounds_run < rounds`` 일 수 있음 — 요청한 totals 로 덮으면 안 됨).
            # 없으면 on_progress 가 마지막으로 보고한 값을 그대로 둔다.
            if outcome.rounds_run is not None:
                record.rounds_done = outcome.rounds_run
            # step 5 — composer 가 있으면 보고서 생성. 호출 직전 status="composing"
            # 으로 전환해 프론트가 "보고서 합성중" 을 명시적으로 표시할 수 있게 한다
            # (이전엔 rounds_done==rounds_total + running 으로 추론, early-exit 사각
            # 있었음). composer 가 None 이면 (fake/tests) 곧장 completed.
            if self._composer is not None:
                record.status = "composing"
                self._persist(record)
                _emit_status()
                composer_outcome = await self._composer(
                    plaza_id=plaza_id,
                    event_log_path=event_log_path,
                    preset=record.preset,
                )
                record.report_markdown = composer_outcome.markdown
                record.report_fallback_used = composer_outcome.fallback_used
                record.tokens_used += composer_outcome.tokens_used
                # composer 가 자기 집계를 outcome 으로 흘려보냈으면 그대로 캐시 —
                # /report 가 같은 events.jsonl 을 다시 안 본다.
                if composer_outcome.aggregation is not None:
                    record.aggregation_cache = composer_outcome.aggregation
            record.status = "completed"
            self._persist(record)
            _emit_status()

        record.task = asyncio.create_task(_drive(), name=f"plaza-{plaza_id}")
        return record

    async def get(self, plaza_id: str) -> PlazaRecord | None:
        async with self._lock:
            return self._records.get(plaza_id)

    async def subscribe(self, plaza_id: str) -> asyncio.Queue[PlazaEvent] | None:
        """SSE 라우트용 — 신규 큐를 만들어 ``record.subscribers`` 에 붙인다.

        plaza 가 없으면 ``None``. 반환된 큐는 호출자가 책임지고
        ``unsubscribe`` 로 떼야 한다 (lifespan = SSE 라우트의 generator).
        """
        async with self._lock:
            record = self._records.get(plaza_id)
            if record is None:
                return None
            queue: asyncio.Queue[PlazaEvent] = asyncio.Queue()
            record.subscribers.append(queue)
            return queue

    async def unsubscribe(self, plaza_id: str, queue: asyncio.Queue[PlazaEvent]) -> None:
        async with self._lock:
            record = self._records.get(plaza_id)
            if record is None:
                return
            with contextlib.suppress(ValueError):
                record.subscribers.remove(queue)

    async def shutdown(self) -> None:
        """프로세스 종료 시 미완료 태스크를 모두 취소 + SQLite 커넥션을 닫는다.

        FastAPI lifespan 의 종료 단계에서 호출 — 테스트는 명시적으로 호출한다.
        취소된 태스크가 ``_drive`` 안의 ``await self._runner(...)`` 도중이면
        record.status 는 마지막 commit 된 값으로 디스크에 남는다 (보통
        ``running`` 또는 ``composing``) — 다음 프로세스 기동 시 hydrate 가
        그 row 를 ``failed`` 로 강제 마킹한다.
        """
        async with self._lock:
            tasks = [r.task for r in self._records.values() if r.task and not r.task.done()]
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await t
        if self._db is not None:
            self._db.close()
            self._db = None


__all__ = [
    "ComposerOutcome",
    "EventType",
    "PlazaComposer",
    "PlazaEvent",
    "PlazaRecord",
    "PlazaRunner",
    "PlazaStore",
    "ProgressCallback",
    "RunnerOutcome",
]
