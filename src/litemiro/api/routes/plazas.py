"""Plaza 라이프사이클 라우트.

- ``POST /api/plazas``                — 시뮬레이션 생성 (background task 시작).
- ``GET  /api/plazas/{id}/status``    — 진행률/상태 조회.
- ``GET  /api/plazas/{id}/report``    — 완료 plaza 의 집계 보고서 (결정적).
- ``GET  /api/plazas/{id}/agents``    — Phase 1 산출의 앵커 리스트 (Casting 화면용).
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import ValidationError

from litemiro.api.models import (
    CreatePlazaRequest,
    CreatePlazaResponse,
    PlazaAgentItem,
    PlazaAgentsResponse,
    PlazaReportResponse,
    PlazaStatusResponse,
)
from litemiro.api.report import build_report
from litemiro.api.store import PlazaStore
from litemiro.phase1.models import OntologyA


def _load_ontology_a(onto_path: Path) -> OntologyA | None:
    """동기 파일 IO + Pydantic 파싱. 라우트가 ``asyncio.to_thread`` 로 감싼다.

    파일이 없으면 ``None`` (라우트가 404 로 변환). 깨진 JSON / 스키마 위반은
    그대로 예외를 던져 호출자가 500 으로 매핑한다.
    """
    if not onto_path.exists():
        return None
    raw = json.loads(onto_path.read_text(encoding="utf-8"))
    return OntologyA.model_validate(raw)


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
        preset=payload.preset,
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


@router.get("/{plaza_id}/report", response_model=PlazaReportResponse)
async def get_report(plaza_id: str, request: Request) -> PlazaReportResponse:
    """완료된 plaza 의 결정적 집계 보고서.

    pending / running 상태에서는 409 — 부분 집계는 의도적으로 막는다 (라운드
    중간 events.jsonl 은 last-line truncated 가능). failed 는 부분 산출물이라도
    돌려준다 — DataAggregator 가 partial-but-valid 를 허용하므로 디버그 가치 있음.
    """
    store = _store(request)
    record = await store.get(plaza_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plaza {plaza_id!r} not found",
        )
    if record.status in {"pending", "running"}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"plaza {plaza_id!r} is still {record.status}",
        )
    return build_report(record)


@router.get("/{plaza_id}/agents", response_model=PlazaAgentsResponse)
async def get_agents(plaza_id: str, request: Request) -> PlazaAgentsResponse:
    """plaza 에 묶인 Phase 1 산출 (``ontology_a_persona.json``) 의 앵커 리스트.

    Casting 화면이 slot 시각화 (이름/역할/이데올로기) 용으로 쓴다. ontology_a 는
    plaza 생성 시점에 이미 존재해야 하므로 (POST /api/plazas 가 path 검증) status
    가 ``pending`` 이어도 의미 있는 응답이 가능 — 라운드 시작 전부터 사용 가능.

    avatar 는 ontology 스키마에 없어 응답에서 빠진다 — 프론트가 ``id`` 해시 같은
    deterministic generator 로 만든다.
    """
    store = _store(request)
    record = await store.get(plaza_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plaza {plaza_id!r} not found",
        )
    onto_path = record.ontology_a_path
    if onto_path is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ontology_a for plaza {plaza_id!r} unavailable",
        )
    try:
        ontology = await asyncio.to_thread(_load_ontology_a, Path(onto_path))
    except (json.JSONDecodeError, ValidationError, OSError) as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"ontology_a parse failed: {type(exc).__name__}",
        ) from exc
    if ontology is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ontology_a for plaza {plaza_id!r} unavailable",
        )
    agents = [
        PlazaAgentItem(
            id=profile.agent_id,
            name=profile.name,
            role=profile.entity_type,
            ideology=profile.ideology,
            topics=list(profile.topics),
        )
        for profile in ontology.agents.values()
    ]
    return PlazaAgentsResponse(plaza_id=plaza_id, agents=agents)


__all__ = ["router"]
