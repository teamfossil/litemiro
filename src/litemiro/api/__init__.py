"""HTTP API — 프론트엔드(Vite/React) 가 시뮬레이션을 띄우고 상태를 받는다.

step 1/4: `/api/health`, `POST /api/plazas`, `GET /api/plazas/{id}/status`.
실 `run_simulation` 은 step 2 이후에 결선한다 — 본 모듈은 ``PlazaRunner`` 추상
주입으로 닫혀 있어 fake 만으로 테스트 가능.
"""

from __future__ import annotations

from litemiro.api.app import create_app
from litemiro.api.runner import RealPlazaRunner
from litemiro.api.store import PlazaRunner, PlazaStore

__all__ = ["PlazaRunner", "PlazaStore", "RealPlazaRunner", "create_app"]
