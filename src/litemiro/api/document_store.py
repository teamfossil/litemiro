"""사용자 업로드 문서의 디스크 저장 + 메타 영속화.

``PlazaStore`` 와 같은 SQLite 파일을 공유한다 — 한 process 안에 두 connection
이 있지만 WAL 모드로 동시 read/write 락 경합이 줄어 문제 없다. 파일 자체는
``files_dir`` 아래에 ``{document_id}{ext}`` 로 저장 — 원본 파일명을 그대로
쓰면 중복/한글 등 OS 차이 골치 아파서 UUID 로 통일하고, 원본 파일명은 row
의 ``filename`` 컬럼에 따로 보관한다.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from litemiro.api import db as _db

if TYPE_CHECKING:
    import sqlite3


class DocumentStore:
    """문서 업로드/조회의 얇은 래퍼.

    인-메모리 캐시는 일부러 두지 않는다 — 모든 read 가 sqlite 1 회 쿼리.
    ``GET /api/documents/{id}`` 가 자주 불릴 가능성이 낮고, 캐시 무효화
    로직을 추가하면 plaza 와 달리 동기화 포인트가 또 생긴다.
    """

    def __init__(self, *, db_path: Path, files_dir: Path) -> None:
        self._db_path = db_path
        self._files_dir = files_dir
        files_dir.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = _db.connect(db_path)

    def upload(self, *, content: bytes, filename: str, mime_type: str) -> _db.DocumentRow:
        """파일 본문을 받아 디스크에 쓰고 row 한 줄 생성. document_id 반환.

        호출자가 크기/MIME validation 을 마쳤다고 가정 — 본 메서드는 디스크/DB
        만 만진다.
        """
        document_id = uuid.uuid4().hex
        digest = hashlib.sha256(content).hexdigest()
        ext = Path(filename).suffix.lower()
        storage_path = self._files_dir / f"{document_id}{ext}"
        storage_path.write_bytes(content)
        row = _db.DocumentRow(
            document_id=document_id,
            filename=filename,
            mime_type=mime_type,
            size_bytes=len(content),
            sha256=digest,
            storage_path=storage_path,
            created_at=datetime.now(UTC).replace(microsecond=0),
        )
        _db.insert_document(self._conn, row)
        return row

    def get(self, document_id: str) -> _db.DocumentRow | None:
        return _db.get_document(self._conn, document_id)

    def list(self) -> list[_db.DocumentRow]:
        return _db.list_documents(self._conn)

    def close(self) -> None:
        self._conn.close()


__all__ = ["DocumentStore"]
