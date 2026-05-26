"""FastAPI 라우터 모음. ``create_app`` 이 모듈별로 include 한다."""

from __future__ import annotations

from litemiro.api.routes.health import router as health_router
from litemiro.api.routes.plazas import router as plazas_router

__all__ = ["health_router", "plazas_router"]
