"""Phase 1 ontology generation 의 큐 + 상태 머신.

``PlazaStore`` 가 plaza 시뮬을 background task 로 돌리는 패턴을 그대로 본떴다.
``OntologyRunner`` callable 을 주입받고 ``generate()`` 가 호출되면 task 를 띄워
``pending → running → completed | failed`` 으로 status 전이. 두 결과 JSON 파일
경로를 row 에 박아 ``CreatePlazaRequest.ontology_id`` 가 그대로 plaza 시작에
넘어갈 수 있게 한다.

real runner 는 ``OntologyPipeline`` 을 호출 — OpenRouter Qwen 콜 분 단위. 단위
테스트는 LLM 의존 없는 fake runner 를 주입해 닫는다.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from litemiro.api import db as _db

if TYPE_CHECKING:
    import sqlite3

    from litemiro.phase1.models import Preset

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class OntologyRunResult:
    """``OntologyRunner`` 가 성공 시 반환할 산출물 경로 + 통계.

    ``OntologySerializer.write`` 가 ``output_dir / ontology_{a,b}_*.json`` 에 두
    파일을 저장하므로 runner 가 그 경로를 그대로 돌려준다. ``agent_count`` 는
    실제 생성된 agent 수 — preset 으로 결정되지만 도중 누락 가능.
    """

    ontology_a_path: Path
    ontology_b_path: Path
    agent_count: int


class OntologyRunner(Protocol):
    async def __call__(
        self,
        *,
        document_path: Path,
        requirement: str,
        preset: Preset,
        output_dir: Path,
    ) -> OntologyRunResult: ...


class OntologyStore:
    """Phase 1 generation 큐. 한 process 안에서 task 를 관리한다.

    SQLite ``ontologies`` 테이블이 SSoT. 부팅 시 ``load_ontologies_recover`` 가
    pending/running row 들을 failed 로 강제 마킹 — 프로세스가 도중에 죽었을 때
    "stuck" 처럼 보이지 않게 한다 (plaza 와 같은 패턴).
    """

    def __init__(
        self,
        *,
        db_path: Path,
        output_dir: Path,
        runner: OntologyRunner,
    ) -> None:
        self._db_path = db_path
        self._output_dir = output_dir
        output_dir.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection = _db.connect(db_path)
        self._runner = runner
        self._tasks: dict[str, asyncio.Task[None]] = {}
        # 부팅 직후 stuck 정리.
        _db.load_ontologies_recover(self._conn)

    def generate(
        self,
        *,
        document_id: str,
        document_path: Path,
        preset: Preset,
        requirement: str,
    ) -> _db.OntologyRow:
        """ontology row INSERT + 백그라운드 task 시작. ``ontology_id`` 즉시 발급.

        호출자는 곧장 응답을 돌려주고, 폴링으로 status 를 추적한다. 단위 테스트
        는 ``await store.wait(ontology_id)`` 로 task 종료를 기다린 뒤 row 를
        다시 ``get`` 해 검증.
        """
        ontology_id = uuid.uuid4().hex
        out_dir = self._output_dir / ontology_id
        now = datetime.now(UTC).replace(microsecond=0)
        row = _db.OntologyRow(
            ontology_id=ontology_id,
            document_id=document_id,
            preset=preset,
            requirement=requirement,
            status="pending",
            ontology_a_path=None,
            ontology_b_path=None,
            agent_count=None,
            error=None,
            created_at=now,
            updated_at=now,
        )
        _db.upsert_ontology(self._conn, row)
        task = asyncio.create_task(self._run(row, document_path, out_dir))
        self._tasks[ontology_id] = task
        return row

    async def _run(self, row: _db.OntologyRow, document_path: Path, out_dir: Path) -> None:
        try:
            row.status = "running"
            _db.upsert_ontology(self._conn, row)
            result = await self._runner(
                document_path=document_path,
                requirement=row.requirement,
                preset=row.preset,
                output_dir=out_dir,
            )
            row.status = "completed"
            row.ontology_a_path = result.ontology_a_path
            row.ontology_b_path = result.ontology_b_path
            row.agent_count = result.agent_count
            row.error = None
            _db.upsert_ontology(self._conn, row)
        except asyncio.CancelledError:
            # shutdown 시 강제 취소된 경우 — failed 로 마킹해 두면 다음 부팅에
            # load_ontologies_recover 가 굳이 또 정리하지 않아도 된다.
            row.status = "failed"
            row.error = "cancelled"
            _db.upsert_ontology(self._conn, row)
            raise
        except Exception as exc:
            log.exception("ontology_generation_failed", extra={"ontology_id": row.ontology_id})
            row.status = "failed"
            row.error = str(exc) or type(exc).__name__
            _db.upsert_ontology(self._conn, row)
        finally:
            self._tasks.pop(row.ontology_id, None)

    def get(self, ontology_id: str) -> _db.OntologyRow | None:
        return _db.get_ontology(self._conn, ontology_id)

    def list(self) -> list[_db.OntologyRow]:
        return _db.list_ontologies(self._conn)

    async def wait(self, ontology_id: str) -> None:
        """단위 테스트용 — 해당 task 가 끝날 때까지 기다린다.

        프로덕션 코드는 폴링 흐름을 따른다 (라우트는 wait 를 부르지 않는다).
        """
        task = self._tasks.get(ontology_id)
        if task is None:
            return
        # 실패한 task 의 예외는 _run 안에서 row 에 기록 — 여기선 무시.
        with contextlib.suppress(Exception):
            await task

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._conn.close()


__all__ = ["OntologyRunResult", "OntologyRunner", "OntologyStore"]
