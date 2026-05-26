"""FastAPI 라우터 모음. ``create_app`` 이 모듈별로 include 한다."""

from __future__ import annotations

from litemiro.api.routes.events import router as events_router
from litemiro.api.routes.health import router as health_router
from litemiro.api.routes.plazas import router as plazas_router

__all__ = ["events_router", "health_router", "plazas_router"]
