"""``PlazaStore`` 의 SQLite 영속화 백엔드.

``base_dir/plazas.db`` 한 파일에 plaza 메타데이터(status/progress/preset/markdown
등) 만 담는다. events.jsonl + checkpoints/ 는 기존대로 디스크에 따로 — DB 는
"프로세스 재시작 후 GET /plazas/{id}/* 가 404 가 아닌 디스크 산출물을 다시
바라보게" 하기 위한 인덱스에 가깝다.

비 영속 필드 (``task`` / ``subscribers`` / ``aggregation_cache``) 는 프로세스
lifetime 안에서만 의미가 있어 컬럼이 없다 — hydrate 시 task/subscribers 는 비고,
aggregation_cache 는 ``/report`` 가 events.jsonl 로 lazy 재집계.

``running`` / ``composing`` / ``pending`` 인 row 가 hydrate 시점에 발견되면
"프로세스가 도중에 죽었다" 는 뜻이므로 ``failed`` + ``error`` 로 강제 마킹한다.
checkpoint 기반 자동 재개는 별도 작업.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast, get_args

from litemiro.api.models import PlazaStatus
from litemiro.phase1.models import Preset

if TYPE_CHECKING:
    from litemiro.api.store import PlazaRecord


@dataclass(frozen=True)
class PlazaSummary:
    """``GET /api/plazas`` 목록용 한 줄. ``PlazaRecord`` 와 달리 큰 본문
    (``report_markdown`` / 파일 경로 4종) 은 빼고 ``created_at`` / ``updated_at``
    을 채운다 — 카드 리스트 화면에서 행마다 markdown KB 를 끌어오는 건 낭비.
    """

    plaza_id: str
    status: PlazaStatus
    rounds_total: int
    rounds_done: int
    label: str | None
    error: str | None
    preset: Preset
    tokens_used: int
    created_at: datetime
    updated_at: datetime


_VALID_STATUSES: frozenset[PlazaStatus] = frozenset(get_args(PlazaStatus))
_INTERRUPTED_STATUSES: frozenset[PlazaStatus] = frozenset({"pending", "running", "composing"})

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS plazas (
    plaza_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    rounds_total INTEGER NOT NULL,
    rounds_done INTEGER NOT NULL DEFAULT 0,
    label TEXT,
    error TEXT,
    tokens_used INTEGER NOT NULL DEFAULT 0,
    preset TEXT NOT NULL,
    ontology_a_path TEXT,
    ontology_b_path TEXT,
    event_log_path TEXT,
    checkpoint_dir TEXT,
    report_markdown TEXT,
    report_fallback_used INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_UPSERT_SQL = """
INSERT INTO plazas (
    plaza_id, status, rounds_total, rounds_done, label, error,
    tokens_used, preset,
    ontology_a_path, ontology_b_path, event_log_path, checkpoint_dir,
    report_markdown, report_fallback_used,
    created_at, updated_at
)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(plaza_id) DO UPDATE SET
    status=excluded.status,
    rounds_total=excluded.rounds_total,
    rounds_done=excluded.rounds_done,
    label=excluded.label,
    error=excluded.error,
    tokens_used=excluded.tokens_used,
    preset=excluded.preset,
    ontology_a_path=excluded.ontology_a_path,
    ontology_b_path=excluded.ontology_b_path,
    event_log_path=excluded.event_log_path,
    checkpoint_dir=excluded.checkpoint_dir,
    report_markdown=excluded.report_markdown,
    report_fallback_used=excluded.report_fallback_used,
    updated_at=excluded.updated_at
"""

_SELECT_ALL_SQL = """
SELECT
    plaza_id, status, rounds_total, rounds_done, label, error,
    tokens_used, preset,
    ontology_a_path, ontology_b_path, event_log_path, checkpoint_dir,
    report_markdown, report_fallback_used,
    created_at, updated_at
FROM plazas
"""

# Summary 전용 SELECT — list 화면 카드에 쓸 컬럼만. ``report_markdown`` 같은
# KB 단위 본문은 빼서 행마다 markdown 을 끌어오는 낭비를 피한다.
_SELECT_SUMMARY_BASE = """
SELECT
    plaza_id, status, rounds_total, rounds_done, label, error,
    tokens_used, preset, created_at, updated_at
FROM plazas
"""

_COUNT_BASE = "SELECT COUNT(*) AS n FROM plazas"


def _utcnow_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def connect(db_path: Path) -> sqlite3.Connection:
    """SQLite 커넥션을 연다 + WAL + foreign keys + row factory 설정.

    WAL 모드는 동시 read/write 시 락 경합을 줄여준다 — 단일 이벤트 루프라도
    SSE 라우트가 동시에 SELECT 를 돌릴 수 있어 의미가 있다.
    ``check_same_thread=False`` 는 우리가 단일 이벤트 루프 (= 단일 thread) 라는
    가정에 기대지만, FastAPI 의 startup/shutdown 이 sub-thread 에서 도는 경우도
    있어 명시적으로 푼다.
    """
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), check_same_thread=False, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(_SCHEMA_SQL)
    return conn


def upsert_record(conn: sqlite3.Connection, record: PlazaRecord) -> None:
    """plaza row 를 INSERT or UPDATE. 호출자가 직접 commit 불필요.

    ``isolation_level=None`` 으로 connect 했기 때문에 sqlite3 가 implicit
    transaction 을 시작하지 않는다 — 매 execute 가 곧 commit.

    ``updated_at`` 은 항상 지금으로 갱신하고 ``record.updated_at`` 에도 반영해서
    in-memory 가 DB row 와 어긋나지 않게 한다. ``created_at`` 은
    ``record.created_at`` 값을 그대로 — INSERT 첫 호출에서 박힌 값을 그 후
    UPSERT 가 ``ON CONFLICT`` 절에서 안 덮는다 (라인 76-77 참고).
    """
    now_dt = datetime.now(UTC).replace(microsecond=0)
    created = record.created_at
    if created.tzinfo is None:
        # naive 가 흘러들어오는 경로는 없지만 안전망 — UTC 가정.
        created = created.replace(tzinfo=UTC)
    record.updated_at = now_dt
    conn.execute(
        _UPSERT_SQL,
        (
            record.plaza_id,
            record.status,
            record.rounds_total,
            record.rounds_done,
            record.label,
            record.error,
            record.tokens_used,
            record.preset.value,
            str(record.ontology_a_path) if record.ontology_a_path else None,
            str(record.ontology_b_path) if record.ontology_b_path else None,
            str(record.event_log_path) if record.event_log_path else None,
            str(record.checkpoint_dir) if record.checkpoint_dir else None,
            record.report_markdown,
            1 if record.report_fallback_used else 0,
            created.isoformat(timespec="seconds"),
            now_dt.isoformat(timespec="seconds"),
        ),
    )


def load_all(conn: sqlite3.Connection) -> list[PlazaRecord]:
    """모든 row 를 ``PlazaRecord`` 로 복원.

    ``pending`` / ``running`` / ``composing`` 인 row 는 중도에 프로세스가 죽은
    것이라 ``failed`` 로 강제 마킹하고 ``error`` 에 그 사실을 적는다 — 클라가
    /status 를 다시 봤을 때 stuck 된 것처럼 보이지 않도록.
    """
    from litemiro.api.store import PlazaRecord  # noqa: PLC0415 — circular avoid

    records: list[PlazaRecord] = []
    for row in list(conn.execute(_SELECT_ALL_SQL)):
        raw_status = row["status"]
        needs_writeback = False
        if raw_status not in _VALID_STATUSES:
            # 미래에 추가된 상태가 옛 코드에서 읽히는 경우. 안전 폴백.
            raw_status = "failed"
            needs_writeback = True
        status = cast(PlazaStatus, raw_status)
        error: str | None = row["error"]
        if status in _INTERRUPTED_STATUSES:
            error = f"process restarted while {status}"
            status = "failed"
            needs_writeback = True
        record = PlazaRecord(
            plaza_id=row["plaza_id"],
            status=status,
            rounds_total=row["rounds_total"],
            rounds_done=row["rounds_done"],
            label=row["label"],
            error=error,
            tokens_used=row["tokens_used"],
            preset=Preset(row["preset"]),
            ontology_a_path=Path(row["ontology_a_path"]) if row["ontology_a_path"] else None,
            ontology_b_path=Path(row["ontology_b_path"]) if row["ontology_b_path"] else None,
            event_log_path=Path(row["event_log_path"]) if row["event_log_path"] else None,
            checkpoint_dir=Path(row["checkpoint_dir"]) if row["checkpoint_dir"] else None,
            report_markdown=row["report_markdown"],
            report_fallback_used=bool(row["report_fallback_used"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )
        if needs_writeback:
            upsert_record(conn, record)
        records.append(record)
    return records


def list_summary(
    conn: sqlite3.Connection,
    *,
    limit: int,
    offset: int,
    status_filter: PlazaStatus | None = None,
) -> tuple[list[PlazaSummary], int]:
    """plaza summary 행 + 필터 적용 후 전체 row 수.

    정렬은 ``created_at DESC, plaza_id DESC`` — 최신 plaza 가 위. ``_utcnow_iso``
    가 ``isoformat(timespec="seconds")`` UTC 라 lexicographic 정렬과 시간 정렬이
    일치 (TZ offset 동일 + zero-padded). ``created_at`` 이 초 단위 truncate 라
    같은 초에 만들어진 두 plaza 가 충분히 가능 — ``plaza_id`` 2 차 키 없이는
    SQLite 가 동률 행 순서를 보장 안 해서 ``LIMIT/OFFSET`` 페이지 경계에 걸린
    plaza 가 누락/중복될 수 있다. in-memory 폴백 경로 (``PlazaStore.list_plazas``)
    도 동일 키 ``(created_at, plaza_id)`` 둘 다 reverse 로 sort 해 두 경로 동치.

    ``status_filter`` 는 단일 PlazaStatus literal — 동일 필터를 COUNT 에도
    걸어 페이지네이션 용 ``total`` 이 "필터 후 전체" 를 가리키게 한다.
    """
    where = ""
    params: tuple[object, ...] = ()
    if status_filter is not None:
        where = " WHERE status = ?"
        params = (status_filter,)
    rows = list(
        conn.execute(
            _SELECT_SUMMARY_BASE
            + where
            + " ORDER BY created_at DESC, plaza_id DESC LIMIT ? OFFSET ?",
            (*params, limit, offset),
        )
    )
    total_row = conn.execute(_COUNT_BASE + where, params).fetchone()
    total = int(total_row["n"]) if total_row is not None else 0
    summaries = [
        PlazaSummary(
            plaza_id=r["plaza_id"],
            status=cast(PlazaStatus, r["status"]),
            rounds_total=r["rounds_total"],
            rounds_done=r["rounds_done"],
            label=r["label"],
            error=r["error"],
            preset=Preset(r["preset"]),
            tokens_used=r["tokens_used"],
            created_at=datetime.fromisoformat(r["created_at"]),
            updated_at=datetime.fromisoformat(r["updated_at"]),
        )
        for r in rows
    ]
    return summaries, total


__all__ = ["PlazaSummary", "connect", "list_summary", "load_all", "upsert_record"]
