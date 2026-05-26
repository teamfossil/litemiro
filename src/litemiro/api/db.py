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
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, cast, get_args

from litemiro.api.models import PlazaStatus
from litemiro.phase1.models import Preset

if TYPE_CHECKING:
    from litemiro.api.store import PlazaRecord

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
    report_markdown, report_fallback_used
FROM plazas
"""


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
    """
    now = _utcnow_iso()
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
            now,
            now,
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
    for row in conn.execute(_SELECT_ALL_SQL):
        raw_status = row["status"]
        if raw_status not in _VALID_STATUSES:
            # 미래에 추가된 상태가 옛 코드에서 읽히는 경우. 안전 폴백.
            raw_status = "failed"
        status = cast(PlazaStatus, raw_status)
        error: str | None = row["error"]
        if status in _INTERRUPTED_STATUSES:
            error = f"process restarted while {status}"
            status = "failed"
        records.append(
            PlazaRecord(
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
            )
        )
    return records


__all__ = ["connect", "load_all", "upsert_record"]
