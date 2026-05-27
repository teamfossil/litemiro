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

import base64
import binascii
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, cast, get_args

from litemiro.api.models import PlazaStatus
from litemiro.phase1.models import Preset

if TYPE_CHECKING:
    from litemiro.api.store import PlazaRecord


OntologyStatus = Literal["pending", "running", "completed", "failed"]
_VALID_ONTOLOGY_STATUSES: frozenset[OntologyStatus] = frozenset(get_args(OntologyStatus))
# Phase 1 도중 프로세스가 죽으면 (= pending/running) 다음 부팅에서 failed 로 강제 마킹.
_INTERRUPTED_ONTOLOGY_STATUSES: frozenset[OntologyStatus] = frozenset({"pending", "running"})


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


@dataclass
class DocumentRow:
    """업로드된 사용자 문서 한 건. ``storage_path`` 는 디스크 위치(절대 경로)."""

    document_id: str
    filename: str
    mime_type: str
    size_bytes: int
    sha256: str
    storage_path: Path
    created_at: datetime


@dataclass
class OntologyRow:
    """Phase 1 generation 한 건. ``status`` 가 ``completed`` 면 두 path 가 채워진다.

    ``agent_count`` 는 ``OntologyA.agent_count`` — preset 으로 결정되지만 Phase 1
    내부 동작에 따라 미달할 수 있어 실측값을 따로 저장. 보고용.
    """

    ontology_id: str
    document_id: str
    preset: Preset
    requirement: str
    status: OntologyStatus
    ontology_a_path: Path | None
    ontology_b_path: Path | None
    agent_count: int | None
    error: str | None
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
-- 목록 정렬 (최신순 + tie-break) 용. ``ORDER BY created_at DESC, plaza_id DESC``
-- 이 row 수에 비례해 풀스캔 → sort 로 굳지 않도록.
CREATE INDEX IF NOT EXISTS idx_plazas_created_id
    ON plazas (created_at DESC, plaza_id DESC);
-- ``?status=`` 필터 단독 사용 시 인덱스 스캔으로 좁힌 뒤 정렬. 동시 사용 시
-- composite (status, created_at DESC) 가 더 빠르지만 행 수가 만 단위 안 갈
-- 시뮬레이션 워크로드 기준 단순 인덱스로 충분.
CREATE INDEX IF NOT EXISTS idx_plazas_status ON plazas (status);

CREATE TABLE IF NOT EXISTS documents (
    document_id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_documents_sha256 ON documents (sha256);

CREATE TABLE IF NOT EXISTS ontologies (
    ontology_id TEXT PRIMARY KEY,
    document_id TEXT NOT NULL,
    preset TEXT NOT NULL,
    requirement TEXT NOT NULL,
    status TEXT NOT NULL,
    ontology_a_path TEXT,
    ontology_b_path TEXT,
    agent_count INTEGER,
    error TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(document_id)
);

CREATE INDEX IF NOT EXISTS idx_ontologies_document ON ontologies (document_id);
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


def encode_cursor(created_at: datetime, plaza_id: str) -> str:
    """``(created_at, plaza_id)`` → opaque cursor 문자열.

    클라가 다음 페이지 요청 시 그대로 ``?cursor=`` 로 돌려보낸다 — 서버만 의미를
    안다 (포맷 변경의 자유를 위해 base64 + ``|`` 구분자). URL-safe & no pad.
    """
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    raw = f"{created_at.isoformat(timespec='seconds')}|{plaza_id}".encode()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def decode_cursor(cursor: str) -> tuple[datetime, str]:
    """opaque cursor → ``(created_at, plaza_id)``. 잘못된 입력은 ``ValueError``.

    클라가 stale cursor 를 들고 와도 단지 다음 페이지가 비어 보일 뿐이라
    silent 한 빈 페이지는 정상 동작 — 디코드 실패만 422 로 응답한다.
    """
    pad = "=" * (-len(cursor) % 4)
    try:
        raw = base64.urlsafe_b64decode(cursor + pad).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError) as exc:
        raise ValueError(f"invalid cursor: {cursor!r}") from exc
    if "|" not in raw:
        raise ValueError(f"invalid cursor payload: {raw!r}")
    iso, plaza_id = raw.rsplit("|", 1)
    if not plaza_id:
        raise ValueError(f"invalid cursor: empty plaza_id from {raw!r}")
    try:
        created_at = datetime.fromisoformat(iso)
    except ValueError as exc:
        raise ValueError(f"invalid cursor timestamp: {iso!r}") from exc
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return created_at, plaza_id


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


def delete_record(conn: sqlite3.Connection, plaza_id: str) -> bool:
    """plaza row 한 건 삭제. row 가 있었으면 True / 없으면 False.

    호출자가 직접 commit 불필요 (``isolation_level=None`` — auto-commit). 디스크
    산출물 (events.jsonl / checkpoints/) 삭제는 ``PlazaStore.delete`` 책임이다 —
    DB layer 는 한 row 만 본다.
    """
    cursor = conn.execute("DELETE FROM plazas WHERE plaza_id = ?", (plaza_id,))
    return cursor.rowcount > 0


def list_summary(
    conn: sqlite3.Connection,
    *,
    limit: int,
    offset: int = 0,
    cursor: tuple[datetime, str] | None = None,
    status_filter: PlazaStatus | None = None,
) -> tuple[list[PlazaSummary], int, str | None]:
    """plaza summary 한 페이지 + ``total`` + ``next_cursor``.

    정렬은 ``created_at DESC, plaza_id DESC`` — 최신 plaza 가 위. ``_utcnow_iso``
    가 ``isoformat(timespec="seconds")`` UTC 라 lexicographic 정렬과 시간 정렬이
    일치 (TZ offset 동일 + zero-padded). ``created_at`` 이 초 단위 truncate 라
    같은 초에 만들어진 두 plaza 가 충분히 가능 — ``plaza_id`` 2 차 키 없이는
    SQLite 가 동률 행 순서를 보장 안 해서 ``LIMIT/OFFSET`` 페이지 경계에 걸린
    plaza 가 누락/중복될 수 있다. in-memory 폴백 경로 (``PlazaStore.list_plazas``)
    도 동일 키 ``(created_at, plaza_id)`` 둘 다 reverse 로 sort 해 두 경로 동치.

    ``cursor`` 가 주어지면 keyset 페이징 — ``offset`` 무시. ``(created_at,
    plaza_id)`` 보다 strictly 작은 행만 본다. 깊은 offset 이 ``LIMIT N OFFSET M``
    의 M 만큼 스캔하던 비용을 인덱스 lookup 한 번으로 줄이는 게 목적. ``next_cursor``
    는 cursor 모드일 때만 채운다 — 마지막 페이지면 ``None``. offset 모드의
    응답은 ``next_cursor=None`` 으로 두고 클라가 limit/offset/total 로 페이지를
    돌게 한다 (두 모드를 한 응답에서 섞지 않아 사용자 코드가 분기 안 헷갈리게).

    ``status_filter`` 는 단일 PlazaStatus literal — 동일 필터를 COUNT 에도
    걸어 ``total`` 이 "필터 후 전체" 를 가리키게 한다. cursor 모드도 ``total``
    은 일관되게 채운다 — 인덱스 덕에 COUNT 비용 작고, 클라가 "총 N건 중 M번째"
    같은 UI 를 유지할 수 있다.
    """
    where_clauses: list[str] = []
    params: list[object] = []
    if status_filter is not None:
        where_clauses.append("status = ?")
        params.append(status_filter)
    if cursor is not None:
        cursor_iso = cursor[0].isoformat(timespec="seconds")
        cursor_id = cursor[1]
        # keyset: 같은 created_at 의 plaza_id tie-break 까지 정확히 다음 행부터.
        where_clauses.append("(created_at < ? OR (created_at = ? AND plaza_id < ?))")
        params.extend([cursor_iso, cursor_iso, cursor_id])
    where_sql = (" WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    # cursor 모드면 offset 은 강제로 0 — keyset 이 위치를 결정.
    effective_offset = 0 if cursor is not None else offset
    rows = list(
        conn.execute(
            _SELECT_SUMMARY_BASE
            + where_sql
            + " ORDER BY created_at DESC, plaza_id DESC LIMIT ? OFFSET ?",
            (*params, limit, effective_offset),
        )
    )
    # COUNT 는 페이지 keyset 과 무관한 "필터 전체" — status_filter 만 반영.
    count_where = ""
    count_params: tuple[object, ...] = ()
    if status_filter is not None:
        count_where = " WHERE status = ?"
        count_params = (status_filter,)
    total_row = conn.execute(_COUNT_BASE + count_where, count_params).fetchone()
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
    next_cursor: str | None = None
    if summaries and len(summaries) == limit:
        # 한 페이지 꽉 찼으면 다음 페이지가 있을 가능성. 비어 있거나 부족하면
        # next_cursor=None — 끝. 한 페이지 정확히 ``limit`` 만큼 차고 그게 끝인
        # 경우는 클라가 다음 호출에서 빈 페이지를 받아 끝남을 확인 (keyset 정석).
        # cursor 모드뿐 아니라 offset 모드에서도 채워서, 클라가 첫 호출(no cursor)
        # 후 두 번째부터 cursor 로 스위치할 수 있게 한다 — infinite scroll 패턴.
        last = summaries[-1]
        next_cursor = encode_cursor(last.created_at, last.plaza_id)
    return summaries, total, next_cursor


_DOCUMENT_INSERT_SQL = """
INSERT INTO documents (
    document_id, filename, mime_type, size_bytes, sha256, storage_path, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_DOCUMENT_SELECT_SQL = """
SELECT document_id, filename, mime_type, size_bytes, sha256, storage_path, created_at
FROM documents
"""


def insert_document(conn: sqlite3.Connection, row: DocumentRow) -> None:
    """문서 한 건 INSERT. 같은 document_id 면 sqlite IntegrityError — 호출자가
    UUID 발급 책임을 지므로 충돌이 나면 발급 측 버그.
    """
    conn.execute(
        _DOCUMENT_INSERT_SQL,
        (
            row.document_id,
            row.filename,
            row.mime_type,
            row.size_bytes,
            row.sha256,
            str(row.storage_path),
            row.created_at.isoformat(timespec="seconds"),
        ),
    )


def get_document(conn: sqlite3.Connection, document_id: str) -> DocumentRow | None:
    cursor = conn.execute(
        _DOCUMENT_SELECT_SQL + " WHERE document_id = ?",
        (document_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return DocumentRow(
        document_id=row["document_id"],
        filename=row["filename"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        sha256=row["sha256"],
        storage_path=Path(row["storage_path"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def list_documents(conn: sqlite3.Connection) -> list[DocumentRow]:
    rows = list(conn.execute(_DOCUMENT_SELECT_SQL + " ORDER BY created_at DESC, document_id DESC"))
    return [
        DocumentRow(
            document_id=r["document_id"],
            filename=r["filename"],
            mime_type=r["mime_type"],
            size_bytes=r["size_bytes"],
            sha256=r["sha256"],
            storage_path=Path(r["storage_path"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]


def delete_document(conn: sqlite3.Connection, document_id: str) -> bool:
    cursor = conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
    return cursor.rowcount > 0


_ONTOLOGY_UPSERT_SQL = """
INSERT INTO ontologies (
    ontology_id, document_id, preset, requirement, status,
    ontology_a_path, ontology_b_path, agent_count, error,
    created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(ontology_id) DO UPDATE SET
    status=excluded.status,
    ontology_a_path=excluded.ontology_a_path,
    ontology_b_path=excluded.ontology_b_path,
    agent_count=excluded.agent_count,
    error=excluded.error,
    updated_at=excluded.updated_at
"""

_ONTOLOGY_SELECT_SQL = """
SELECT ontology_id, document_id, preset, requirement, status,
       ontology_a_path, ontology_b_path, agent_count, error,
       created_at, updated_at
FROM ontologies
"""


def upsert_ontology(conn: sqlite3.Connection, row: OntologyRow) -> None:
    """ontology row INSERT or UPDATE. ``updated_at`` 은 항상 지금으로 갱신.

    ``isolation_level=None`` (auto-commit) 이라 호출자가 따로 commit 안 해도 됨.
    """
    now_dt = datetime.now(UTC).replace(microsecond=0)
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    row.updated_at = now_dt
    conn.execute(
        _ONTOLOGY_UPSERT_SQL,
        (
            row.ontology_id,
            row.document_id,
            row.preset.value,
            row.requirement,
            row.status,
            str(row.ontology_a_path) if row.ontology_a_path else None,
            str(row.ontology_b_path) if row.ontology_b_path else None,
            row.agent_count,
            row.error,
            created.isoformat(timespec="seconds"),
            now_dt.isoformat(timespec="seconds"),
        ),
    )


def _row_to_ontology(row: sqlite3.Row) -> OntologyRow:
    raw_status = row["status"]
    if raw_status not in _VALID_ONTOLOGY_STATUSES:
        # 옛 코드가 새 status 를 만난 경우 폴백 — 클라가 "stuck" 처럼 보이지 않도록.
        raw_status = "failed"
    return OntologyRow(
        ontology_id=row["ontology_id"],
        document_id=row["document_id"],
        preset=Preset(row["preset"]),
        requirement=row["requirement"],
        status=cast(OntologyStatus, raw_status),
        ontology_a_path=Path(row["ontology_a_path"]) if row["ontology_a_path"] else None,
        ontology_b_path=Path(row["ontology_b_path"]) if row["ontology_b_path"] else None,
        agent_count=row["agent_count"],
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def get_ontology(conn: sqlite3.Connection, ontology_id: str) -> OntologyRow | None:
    cursor = conn.execute(
        _ONTOLOGY_SELECT_SQL + " WHERE ontology_id = ?",
        (ontology_id,),
    )
    row = cursor.fetchone()
    return _row_to_ontology(row) if row is not None else None


def list_ontologies(conn: sqlite3.Connection) -> list[OntologyRow]:
    rows = list(conn.execute(_ONTOLOGY_SELECT_SQL + " ORDER BY created_at DESC, ontology_id DESC"))
    return [_row_to_ontology(r) for r in rows]


def load_ontologies_recover(conn: sqlite3.Connection) -> list[OntologyRow]:
    """모든 ontology row 를 복원. pending/running 은 ``failed`` 로 강제 마킹 +
    error 에 그 사실을 적어 클라가 stuck 처럼 보지 않게 한다 (plazas 와 동일 패턴).
    """
    out: list[OntologyRow] = []
    for r in list(conn.execute(_ONTOLOGY_SELECT_SQL)):
        row = _row_to_ontology(r)
        if row.status in _INTERRUPTED_ONTOLOGY_STATUSES:
            row.status = "failed"
            row.error = f"process restarted while {r['status']}"
            upsert_ontology(conn, row)
        out.append(row)
    return out


_DOCUMENT_INSERT_SQL = """
INSERT INTO documents (
    document_id, filename, mime_type, size_bytes, sha256, storage_path, created_at
) VALUES (?, ?, ?, ?, ?, ?, ?)
"""

_DOCUMENT_SELECT_SQL = """
SELECT document_id, filename, mime_type, size_bytes, sha256, storage_path, created_at
FROM documents
"""


def insert_document(conn: sqlite3.Connection, row: DocumentRow) -> None:
    """문서 한 건 INSERT. 같은 document_id 면 sqlite IntegrityError — 호출자가
    UUID 발급 책임을 지므로 충돌이 나면 발급 측 버그.
    """
    conn.execute(
        _DOCUMENT_INSERT_SQL,
        (
            row.document_id,
            row.filename,
            row.mime_type,
            row.size_bytes,
            row.sha256,
            str(row.storage_path),
            row.created_at.isoformat(timespec="seconds"),
        ),
    )


def get_document(conn: sqlite3.Connection, document_id: str) -> DocumentRow | None:
    cursor = conn.execute(
        _DOCUMENT_SELECT_SQL + " WHERE document_id = ?",
        (document_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return DocumentRow(
        document_id=row["document_id"],
        filename=row["filename"],
        mime_type=row["mime_type"],
        size_bytes=row["size_bytes"],
        sha256=row["sha256"],
        storage_path=Path(row["storage_path"]),
        created_at=datetime.fromisoformat(row["created_at"]),
    )


def list_documents(conn: sqlite3.Connection) -> list[DocumentRow]:
    rows = list(conn.execute(_DOCUMENT_SELECT_SQL + " ORDER BY created_at DESC, document_id DESC"))
    return [
        DocumentRow(
            document_id=r["document_id"],
            filename=r["filename"],
            mime_type=r["mime_type"],
            size_bytes=r["size_bytes"],
            sha256=r["sha256"],
            storage_path=Path(r["storage_path"]),
            created_at=datetime.fromisoformat(r["created_at"]),
        )
        for r in rows
    ]


def delete_document(conn: sqlite3.Connection, document_id: str) -> bool:
    cursor = conn.execute("DELETE FROM documents WHERE document_id = ?", (document_id,))
    return cursor.rowcount > 0


_ONTOLOGY_UPSERT_SQL = """
INSERT INTO ontologies (
    ontology_id, document_id, preset, requirement, status,
    ontology_a_path, ontology_b_path, agent_count, error,
    created_at, updated_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(ontology_id) DO UPDATE SET
    status=excluded.status,
    ontology_a_path=excluded.ontology_a_path,
    ontology_b_path=excluded.ontology_b_path,
    agent_count=excluded.agent_count,
    error=excluded.error,
    updated_at=excluded.updated_at
"""

_ONTOLOGY_SELECT_SQL = """
SELECT ontology_id, document_id, preset, requirement, status,
       ontology_a_path, ontology_b_path, agent_count, error,
       created_at, updated_at
FROM ontologies
"""


def upsert_ontology(conn: sqlite3.Connection, row: OntologyRow) -> None:
    """ontology row INSERT or UPDATE. ``updated_at`` 은 항상 지금으로 갱신.

    ``isolation_level=None`` (auto-commit) 이라 호출자가 따로 commit 안 해도 됨.
    """
    now_dt = datetime.now(UTC).replace(microsecond=0)
    created = row.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    row.updated_at = now_dt
    conn.execute(
        _ONTOLOGY_UPSERT_SQL,
        (
            row.ontology_id,
            row.document_id,
            row.preset.value,
            row.requirement,
            row.status,
            str(row.ontology_a_path) if row.ontology_a_path else None,
            str(row.ontology_b_path) if row.ontology_b_path else None,
            row.agent_count,
            row.error,
            created.isoformat(timespec="seconds"),
            now_dt.isoformat(timespec="seconds"),
        ),
    )


def _row_to_ontology(row: sqlite3.Row) -> OntologyRow:
    raw_status = row["status"]
    if raw_status not in _VALID_ONTOLOGY_STATUSES:
        # 옛 코드가 새 status 를 만난 경우 폴백 — 클라가 "stuck" 처럼 보이지 않도록.
        raw_status = "failed"
    return OntologyRow(
        ontology_id=row["ontology_id"],
        document_id=row["document_id"],
        preset=Preset(row["preset"]),
        requirement=row["requirement"],
        status=cast(OntologyStatus, raw_status),
        ontology_a_path=Path(row["ontology_a_path"]) if row["ontology_a_path"] else None,
        ontology_b_path=Path(row["ontology_b_path"]) if row["ontology_b_path"] else None,
        agent_count=row["agent_count"],
        error=row["error"],
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def get_ontology(conn: sqlite3.Connection, ontology_id: str) -> OntologyRow | None:
    cursor = conn.execute(
        _ONTOLOGY_SELECT_SQL + " WHERE ontology_id = ?",
        (ontology_id,),
    )
    row = cursor.fetchone()
    return _row_to_ontology(row) if row is not None else None


def list_ontologies(conn: sqlite3.Connection) -> list[OntologyRow]:
    rows = list(conn.execute(_ONTOLOGY_SELECT_SQL + " ORDER BY created_at DESC, ontology_id DESC"))
    return [_row_to_ontology(r) for r in rows]


def load_ontologies_recover(conn: sqlite3.Connection) -> list[OntologyRow]:
    """모든 ontology row 를 복원. pending/running 은 ``failed`` 로 강제 마킹 +
    error 에 그 사실을 적어 클라가 stuck 처럼 보지 않게 한다 (plazas 와 동일 패턴).
    """
    out: list[OntologyRow] = []
    for r in list(conn.execute(_ONTOLOGY_SELECT_SQL)):
        row = _row_to_ontology(r)
        if row.status in _INTERRUPTED_ONTOLOGY_STATUSES:
            row.status = "failed"
            row.error = f"process restarted while {r['status']}"
            upsert_ontology(conn, row)
        out.append(row)
    return out


__all__ = [
    "DocumentRow",
    "OntologyRow",
    "OntologyStatus",
    "PlazaSummary",
    "connect",
    "decode_cursor",
    "delete_document",
    "delete_record",
    "encode_cursor",
    "get_document",
    "get_ontology",
    "insert_document",
    "list_documents",
    "list_ontologies",
    "list_summary",
    "load_all",
    "load_ontologies_recover",
    "upsert_ontology",
    "upsert_record",
]
