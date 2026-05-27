"""FastAPI 앱 팩토리.

``PlazaRunner`` / ``OntologyRunner`` 를 주입할 수 있게 만들어 두면:

- 실 배포는 `litemiro.api.__main__` 가 real runner 들을 넘기고
- 단위 테스트는 fake runner 들을 넘겨 외부 의존(LLM/embedder) 없이 닫힌다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from litemiro.api.document_store import DocumentStore
from litemiro.api.ontology_store import OntologyStore
from litemiro.api.routes import (
    documents_router,
    events_router,
    health_router,
    ontologies_router,
    plazas_router,
)
from litemiro.api.store import PlazaStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from litemiro.api.ontology_store import OntologyRunner
    from litemiro.api.store import PlazaComposer, PlazaRunner


def create_app(
    *,
    runner: PlazaRunner,
    base_dir: Path,
    composer: PlazaComposer | None = None,
    ontology_runner: OntologyRunner | None = None,
    cors_origins: Sequence[str] = ("http://localhost:5173",),
    db_path: Path | None = None,
) -> FastAPI:
    """앱 인스턴스 생성. ``runner`` 는 plaza 한 건을 처리하는 콜러블.

    ``base_dir`` 아래에 plaza 별 events.jsonl + checkpoints/ 가 저장된다.
    plaza 메타데이터(상태/progress/preset/markdown) 는 ``base_dir/plazas.db``
    (SQLite) 로 영속 — 프로세스 재시작 후에도 GET /status / /report 가 살아있다.
    ``db_path`` 를 명시하면 그 경로를 그대로 쓴다 (테스트 격리용).
    ``composer`` 가 주어지면 sim 완료 직후 호출돼 Markdown 보고서를 채운다.
    fake 서버나 단위 테스트는 ``None`` 으로 두면 통계 응답만 떨어진다.

    ``ontology_runner`` 가 주어지면 ``/api/ontologies`` 가 백그라운드 Phase 1
    을 돌려 사용자 PDF 기반 ontology 를 만든다. ``None`` 이면 ``/api/documents``
    업로드까지만 살아있고 ``POST /api/ontologies`` 는 503 — fake 서버용.
    업로드된 자료는 ``base_dir/documents/`` 에, 결과 ontology JSON 두 개는
    ``base_dir/ontologies/{ontology_id}/`` 에 떨어진다.

    CORS 기본값은 Vite 개발 서버(5173). 배포 환경에서는 호출자가 명시.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    resolved_db_path = db_path if db_path is not None else base_dir / "plazas.db"
    plaza_store = PlazaStore(
        runner=runner,
        base_dir=base_dir,
        composer=composer,
        db_path=resolved_db_path,
    )
    document_store = DocumentStore(
        db_path=resolved_db_path,
        files_dir=base_dir / "documents",
    )
    ontology_store: OntologyStore | None = None
    if ontology_runner is not None:
        ontology_store = OntologyStore(
            db_path=resolved_db_path,
            output_dir=base_dir / "ontologies",
            runner=ontology_runner,
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.plaza_store = plaza_store
        app.state.document_store = document_store
        app.state.ontology_store = ontology_store
        try:
            yield
        finally:
            await plaza_store.shutdown()
            document_store.close()
            if ontology_store is not None:
                await ontology_store.shutdown()

    app = FastAPI(
        title="Litemiro API",
        version="0.1.0",
        description="Mirofish 시뮬레이션을 띄우고 상태를 조회하는 HTTP API.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(plazas_router)
    app.include_router(events_router)
    app.include_router(documents_router)
    if ontology_store is not None:
        app.include_router(ontologies_router)
    return app


__all__ = ["create_app"]
