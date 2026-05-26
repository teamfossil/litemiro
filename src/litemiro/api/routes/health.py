"""``GET /api/health`` — 프론트 polling / 배포 헬스체크 용."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

from fastapi import APIRouter

from litemiro.api.models import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


def _package_version() -> str:
    try:
        return version("litemiro")
    except PackageNotFoundError:
        return "0.0.0+unknown"


@router.get("/health", response_model=HealthResponse)
async def health() -> HealthResponse:
    return HealthResponse(version=_package_version())


__all__ = ["router"]
