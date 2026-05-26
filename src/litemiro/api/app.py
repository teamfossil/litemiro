"""FastAPI 앱 팩토리.

``PlazaRunner`` 를 주입할 수 있게 만들어 두면:

- 실 배포는 `litemiro.api.__main__` 가 `run_simulation` 어댑터를 넘기고
- 단위 테스트는 fake runner 를 넘겨 외부 의존(LLM/embedder) 없이 닫힌다.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from litemiro.api.routes import events_router, health_router, plazas_router
from litemiro.api.store import PlazaStore

if TYPE_CHECKING:
    from collections.abc import Sequence

    from litemiro.api.store import PlazaComposer, PlazaRunner


def create_app(
    *,
    runner: PlazaRunner,
    base_dir: Path,
    composer: PlazaComposer | None = None,
    cors_origins: Sequence[str] = ("http://localhost:5173",),
) -> FastAPI:
    """앱 인스턴스 생성. ``runner`` 는 plaza 한 건을 처리하는 콜러블.

    ``base_dir`` 아래에 plaza 별 events.jsonl + checkpoints/ 가 저장된다.
    ``composer`` 가 주어지면 sim 완료 직후 호출돼 Markdown 보고서를 채운다.
    fake 서버나 단위 테스트는 ``None`` 으로 두면 통계 응답만 떨어진다.
    CORS 기본값은 Vite 개발 서버(5173). 배포 환경에서는 호출자가 명시.
    """
    base_dir.mkdir(parents=True, exist_ok=True)
    store = PlazaStore(runner=runner, base_dir=base_dir, composer=composer)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.plaza_store = store
        try:
            yield
        finally:
            await store.shutdown()

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
    return app


__all__ = ["create_app"]
