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
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import ValidationError

from litemiro.api import db as _db
from litemiro.api.composer import ComposerOutcome
from litemiro.api.models import PlazaStatus
from litemiro.models import ActionType, RoundEvent
from litemiro.phase1.models import Preset
from litemiro.phase3.models import AggregationResult


def _utcnow() -> datetime:
    # microsecond 제거 — SQLite 직렬화가 ``isoformat(timespec="seconds")`` 라
    # in-memory 와 hydrate 후 record 의 created_at 정밀도가 어긋나지 않게 한다.
    return datetime.now(UTC).replace(microsecond=0)


# SSE 이벤트의 분류 —
#  * progress:          라운드 진행률 갱신 (rounds_done 증가)
#  * status:            상태 머신 전환 (running/composing/completed/failed).
#                       terminal ("completed"/"failed") 가 들어오면 스트림 종료
#                       신호로도 같이 쓰인다.
#  * action:            events.jsonl 의 한 줄 (한 agent 의 한 액션) — 라이브 push.
#  * actions_snapshot:  연결 직후 최근 N 건 액션을 한 번에 — 재연결 시 빈 피드 회피.
EventType = Literal["progress", "status", "action", "actions_snapshot"]

# 재연결 시 연결 직후 emit 할 최근 액션 수 상한. events.jsonl 마지막에서부터
# 위로 훑으면서 DO_NOTHING 제외하고 이 수만큼만 모아 보낸다.
SNAPSHOT_ACTION_LIMIT = 40

# action tail task 의 폴링 간격. EventLogger 가 line flush 라 partial-but-valid 인
# 끝 라인은 parse 시도조차 안 하지만, 새 라인 검출 latency 가 이 값에 좌우된다.
# 라운드 wall-clock (수 초) 에 비해 50 ms 면 사실상 즉시. 테스트는 monkeypatch.
_TAIL_POLL_INTERVAL_SECONDS = 0.05


def _read_since(path: Path, offset: int) -> tuple[str, int]:
    """``offset`` byte 이후의 events.jsonl 본문 + 새 offset.

    파일이 없으면 ``("", offset)`` — tail 시작 직후 한두 tick 은 file 이 없을
    수 있다 (runner 가 첫 라인 쓰기 전). bytes → str 디코드는 ``errors="replace"``
    로 — 멀티바이트가 라인 중간에서 잘려도 다음 tick 의 flush 가 도착하면
    이어 붙은 buffer 에서 정상 라인으로 복구된다.
    """
    if not path.exists():
        return "", offset
    with path.open("rb") as f:
        f.seek(offset)
        data = f.read()
    return data.decode("utf-8", errors="replace"), offset + len(data)


def _action_payload(event: RoundEvent) -> dict[str, Any]:
    """``RoundEvent`` → SSE ``data`` 페이로드 (action / actions_snapshot 공용).

    여기서 한 번에 직렬화 형식을 고정해 라이브 push (``event: action``) 와 재연결
    스냅샷 (``event: actions_snapshot``) 의 element shape 가 어긋나지 않게 한다.
    """
    action = event.action
    return {
        "round_num": event.round_num,
        "agent_id": event.agent_id,
        "type": action.type.value,
        "target_post_id": action.target_post_id,
        "target_agent_id": action.target_agent_id,
        "content": action.content,
        "timestamp": event.timestamp.isoformat(),
    }


def _parse_event_log(path: Path) -> list[RoundEvent]:
    """events.jsonl 을 통째로 읽어 RoundEvent 리스트로. ``load_recent_actions`` 용.

    파일이 없거나 빈 경우 ``[]``. 깨진 라인은 건너뛴다 (last-line truncate /
    프로세스 사망 직후 partial write). tail 과 같은 관용 — events.jsonl 자체가
    line-level flush 라 잘린 라인은 끝에만 생긴다.

    파일 끝부터 거꾸로 읽는 게 메모리상 더 좋지만, 시뮬레이션 한 건의
    events.jsonl 은 라운드 200 x agent 100 ~= 20k 라인 (수 MB) 이라 단순 전부
    읽기로 충분. 거꾸로 읽기 코드는 깨진 라인 처리가 복잡해 가치가 낮다.
    """
    if not path.exists():
        return []
    out: list[RoundEvent] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            out.append(RoundEvent.model_validate_json(line))
        except ValidationError:
            continue
    return out


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
    # ``GET /api/plazas`` 목록 정렬용. INSERT 시점에 박고 그 후 mutation 마다
    # ``upsert_record`` 가 ``updated_at`` 만 ``_utcnow`` 로 덮는다. SQLite 영속화
    # 시 ``isoformat(timespec="seconds")`` 로 저장되므로 마이크로초 정밀도는
    # 디스크 직렬화 후 잘린다 — 같은 라운드 안에서 두 record 가 동일 timestamp
    # 가 될 수 있지만 정렬 안정성은 plaza_id 가 tie-break.
    created_at: datetime = field(default_factory=_utcnow)
    updated_at: datetime = field(default_factory=_utcnow)


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

    async def _tail_event_log(self, record: PlazaRecord, stop_event: asyncio.Event) -> None:
        """events.jsonl 을 폴링하면서 새 라인을 ``action`` SSE 로 broadcast.

        runner Protocol 을 건드리지 않으려고 callback 대신 file tail 로 갔다 —
        EventLogger 가 line-level flush 라 끝 라인이 잘려도 ``\\n`` 도착 전엔
        parse 시도조차 안 한다 (partial-but-valid 유지).

        ``stop_event`` 가 set 되면 drain 한 번 더 돌리고 종료 — 호출자가 await
        해서 terminal status emit 전에 마지막 라인까지 broadcast 됐음을 보장한다.

        ``event_log_path`` 가 None (fake / 비영속 fake 테스트) 이면 즉시 종료.
        """
        path = record.event_log_path
        if path is None:
            return
        offset = 0
        pending = ""

        async def drain() -> None:
            nonlocal offset, pending
            try:
                new_text, offset = await asyncio.to_thread(_read_since, path, offset)
            except OSError:
                # 일시적 IO 실패는 다음 tick 에 다시 시도. tail 을 죽이면 라이브
                # 스트림이 끊겨 사용자 경험이 더 나빠진다.
                return
            if not new_text:
                return
            pending += new_text
            while "\n" in pending:
                line, pending = pending.split("\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    event = RoundEvent.model_validate_json(line)
                except ValidationError:
                    # 깨진 라인은 건너뛴다 — 다음 라인은 다시 정상일 수 있다.
                    continue
                # DO_NOTHING 은 events.jsonl 에는 남기지만 (집계/재현성) SSE
                # 스트림에서는 노이즈 → 부감 뷰 깜빡임/토스트 의미 없음 → 컷.
                if event.action.type is ActionType.DO_NOTHING:
                    continue
                self._broadcast(
                    record,
                    PlazaEvent(type="action", data=_action_payload(event)),
                )

        while not stop_event.is_set():
            await drain()
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=_TAIL_POLL_INTERVAL_SECONDS)
            except TimeoutError:
                continue
        # final drain — stop 직전 sleep window 안에 막판에 들어온 라인을 회수.
        await drain()

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

    async def create(  # noqa: PLR0915 — inner _drive 가 6 단계 상태 머신이라
        # 통째로 펼친 게 메서드 분리보다 읽기 쉽다. 별도 메서드로 빼려면 closure
        # (record / plaza_id / 경로 6 개 등) 를 전부 인자로 풀어야 해서 시그니처가
        # 더 어지러워진다.
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
            # action SSE tail — runner 와 같은 lifetime. finally 의 stop+await
            # 가 terminal status emit 직전 마지막 라인까지 broadcast 를 보장.
            stop_tail = asyncio.Event()
            tail_task = asyncio.create_task(
                self._tail_event_log(record, stop_tail),
                name=f"plaza-tail-{plaza_id}",
            )
            try:
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
                    return
                record.tokens_used = outcome.tokens_used
                # outcome.rounds_run 이 있으면 그걸 신뢰 (early-exit 인 경우
                # ``rounds_run < rounds`` 일 수 있음 — 요청한 totals 로 덮으면 안 됨).
                # 없으면 on_progress 가 마지막으로 보고한 값을 그대로 둔다.
                if outcome.rounds_run is not None:
                    record.rounds_done = outcome.rounds_run
                # step 5 — composer 가 있으면 보고서 생성. 호출 직전
                # status="composing" 으로 전환해 프론트가 "보고서 합성중" 을
                # 명시적으로 표시할 수 있게 한다 (이전엔 rounds_done==rounds_total
                # + running 으로 추론, early-exit 사각 있었음). composer 가 None
                # 이면 (fake/tests) 곧장 completed.
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
                    # composer 가 자기 집계를 outcome 으로 흘려보냈으면 그대로
                    # 캐시 — /report 가 같은 events.jsonl 을 다시 안 본다.
                    if composer_outcome.aggregation is not None:
                        record.aggregation_cache = composer_outcome.aggregation
                record.status = "completed"
                self._persist(record)
            finally:
                # tail 이 마지막 drain 까지 완료된 뒤 terminal status emit —
                # 클라이언트 입장에서 마지막 action 이 항상 terminal 보다 먼저.
                stop_tail.set()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await tail_task
                _emit_status()

        record.task = asyncio.create_task(_drive(), name=f"plaza-{plaza_id}")
        return record

    async def get(self, plaza_id: str) -> PlazaRecord | None:
        async with self._lock:
            return self._records.get(plaza_id)

    async def load_recent_actions(
        self,
        plaza_id: str,
        *,
        limit: int = SNAPSHOT_ACTION_LIMIT,
    ) -> list[dict[str, Any]]:
        """events.jsonl 의 최근 ``limit`` 건 액션 페이로드 (DO_NOTHING 제외).

        SSE 라우트가 재연결 직후 ``event: actions_snapshot`` 으로 한 번 흘리는 용도.
        기존 라이브 push 와 같은 element shape (``_action_payload``) 를 쓴다.
        events.jsonl 자체가 없거나 (fake / 초기) 빈 파일이면 ``[]``.

        파일 IO + 파싱이 동기 비용이라 ``asyncio.to_thread`` 로 떼서 SSE 라우트
        진입을 막지 않는다. 마지막 N 건은 원래 순서 (시간 오름차순) 로 돌려준다 —
        프론트가 그대로 위→아래 / 왼→오 로 그리기만 하면 됨.
        """
        record = await self.get(plaza_id)
        if record is None or record.event_log_path is None:
            return []
        path = record.event_log_path
        events = await asyncio.to_thread(_parse_event_log, path)
        filtered = [e for e in events if e.action.type is not ActionType.DO_NOTHING]
        tail = filtered[-limit:] if limit > 0 else []
        return [_action_payload(e) for e in tail]

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

    async def list_plazas(
        self,
        *,
        limit: int,
        offset: int,
        status_filter: PlazaStatus | None = None,
    ) -> tuple[list[_db.PlazaSummary], int]:
        """``GET /api/plazas`` 백킹 — 최신 plaza 가 위.

        영속 모드 (``self._db is not None``) 는 SQLite SELECT 로 정렬·필터·페이지
        처리. 비영속 모드 (단위 테스트) 는 ``_records`` 를 ``created_at`` desc
        로 정렬 — INSERT 시점에 박힌 값을 그대로 쓰므로 영속 모드와 결과 순서가
        같다. tie-break 는 ``plaza_id`` (UUID hex) 로 결정성 확보.
        """
        async with self._lock:
            if self._db is not None:
                return _db.list_summary(
                    self._db,
                    limit=limit,
                    offset=offset,
                    status_filter=status_filter,
                )
            records = list(self._records.values())
            if status_filter is not None:
                records = [r for r in records if r.status == status_filter]
            total = len(records)
            records.sort(key=lambda r: (r.created_at, r.plaza_id), reverse=True)
            page = records[offset : offset + limit]
            summaries = [
                _db.PlazaSummary(
                    plaza_id=r.plaza_id,
                    status=r.status,
                    rounds_total=r.rounds_total,
                    rounds_done=r.rounds_done,
                    label=r.label,
                    error=r.error,
                    preset=r.preset,
                    tokens_used=r.tokens_used,
                    created_at=r.created_at,
                    updated_at=r.updated_at,
                )
                for r in page
            ]
            return summaries, total

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
