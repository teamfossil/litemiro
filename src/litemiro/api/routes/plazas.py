"""Plaza 라이프사이클 라우트.

- ``POST /api/plazas``        — 시뮬레이션 생성 (background task 시작).
- ``GET  /api/plazas/{id}/status`` — 진행률/상태 조회.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status

from litemiro.api.models import (
    CreatePlazaRequest,
    CreatePlazaResponse,
    PlazaStatusResponse,
)
from litemiro.api.store import PlazaStore

router = APIRouter(prefix="/api/plazas", tags=["plazas"])


def _store(request: Request) -> PlazaStore:
    store = getattr(request.app.state, "plaza_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="plaza store not initialised",
        )
    return store  # type: ignore[no-any-return]


@router.post(
    "",
    response_model=CreatePlazaResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_plaza(payload: CreatePlazaRequest, request: Request) -> CreatePlazaResponse:
    store = _store(request)
    record = await store.create(
        ontology_a_path=Path(payload.ontology_a_path),
        ontology_b_path=Path(payload.ontology_b_path),
        rounds=payload.rounds,
        label=payload.label,
    )
    return CreatePlazaResponse(plaza_id=record.plaza_id, status=record.status)


@router.get("/{plaza_id}/status", response_model=PlazaStatusResponse)
async def get_status(plaza_id: str, request: Request) -> PlazaStatusResponse:
    store = _store(request)
    record = await store.get(plaza_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plaza {plaza_id!r} not found",
        )
    return PlazaStatusResponse(
        plaza_id=record.plaza_id,
        status=record.status,
        rounds_total=record.rounds_total,
        rounds_done=record.rounds_done,
        label=record.label,
        error=record.error,
    )


__all__ = ["router"]
