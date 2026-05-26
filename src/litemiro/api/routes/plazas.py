"""Plaza 라이프사이클 라우트.

- ``POST /api/plazas``                — 시뮬레이션 생성 (background task 시작).
- ``GET  /api/plazas``                — 최신순 plaza 목록 (이력 화면용).
- ``GET  /api/plazas/{id}/status``    — 진행률/상태 조회.
- ``GET  /api/plazas/{id}/report``    — 완료 plaza 의 집계 보고서 (결정적).
- ``GET  /api/plazas/{id}/agents``    — Phase 1 산출의 앵커 리스트 (Casting 화면용).
- ``GET  /api/plazas/{id}/layout``    — 종료 plaza 의 부감 뷰 좌표 (Plaza 화면용).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import ValidationError

from litemiro.api.layout import compute_layout, plaza_seed
from litemiro.api.models import (
    CreatePlazaRequest,
    CreatePlazaResponse,
    PlazaAgentItem,
    PlazaAgentsResponse,
    PlazaLayoutAgentItem,
    PlazaLayoutResponse,
    PlazaListResponse,
    PlazaReportResponse,
    PlazaStatus,
    PlazaStatusResponse,
    PlazaSummaryItem,
)
from litemiro.api.report import build_report
from litemiro.api.sample_fixtures import (
    DEFAULT_ONTOLOGY_A_PATH,
    DEFAULT_ONTOLOGY_B_PATH,
)
from litemiro.api.store import PlazaStore
from litemiro.models import ActionType, RoundEvent
from litemiro.phase1.models import OntologyA


def _avatar_seed(agent_id: str) -> int:
    """``agent_id`` 를 결정적 uint32 시드로. sha256 의 앞 4바이트.

    Python ``hash()`` 는 PYTHONHASHSEED 영향을 받아 프로세스 재시작 시 깨진다 —
    프론트가 reload 때마다 다른 아바타를 보면 안 되므로 sha256 사용.
    """
    return int.from_bytes(hashlib.sha256(agent_id.encode("utf-8")).digest()[:4], "big")


def _load_ontology_a(onto_path: Path) -> OntologyA | None:
    """동기 파일 IO + Pydantic 파싱. 라우트가 ``asyncio.to_thread`` 로 감싼다.

    파일이 없으면 ``None`` (라우트가 404 로 변환). 깨진 JSON / 스키마 위반은
    그대로 예외를 던져 호출자가 500 으로 매핑한다.
    """
    if not onto_path.exists():
        return None
    raw = json.loads(onto_path.read_text(encoding="utf-8"))
    return OntologyA.model_validate(raw)


def _read_follow_edges(path: Path) -> tuple[list[tuple[str, str]], dict[str, int]]:
    """events.jsonl 에서 FOLLOW 엣지 + 받은 follow 수 추출.

    파일 부재 / 빈 파일 → ``([], {})`` — layout 은 엣지 0 으로도 결정적이라
    호출자가 그대로 진행. last-line truncate 같은 파싱 실패는 그 라인만 건너뛴다.
    """
    edges: list[tuple[str, str]] = []
    influence: dict[str, int] = {}
    if not path.exists():
        return edges, influence
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            event = RoundEvent.model_validate_json(line)
        except ValidationError:
            continue
        target = event.action.target_agent_id
        if event.action.type is ActionType.FOLLOW and target is not None:
            edges.append((event.agent_id, target))
            influence[target] = influence.get(target, 0) + 1
    return edges, influence


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
    # 두 경로 모두 생략 가능 — 프론트 Seed 화면 같이 항상 같은 sample 을 쓰는
    # 호출 측이 dummy path 를 매번 박지 않게 한다. 명시된 경로는 그대로,
    # ``None`` 은 repo 의 dev fixture 로 폴백 (``sample_fixtures``).
    ontology_a_path = (
        Path(payload.ontology_a_path) if payload.ontology_a_path else DEFAULT_ONTOLOGY_A_PATH
    )
    ontology_b_path = (
        Path(payload.ontology_b_path) if payload.ontology_b_path else DEFAULT_ONTOLOGY_B_PATH
    )
    record = await store.create(
        ontology_a_path=ontology_a_path,
        ontology_b_path=ontology_b_path,
        rounds=payload.rounds,
        label=payload.label,
        preset=payload.preset,
    )
    return CreatePlazaResponse(plaza_id=record.plaza_id, status=record.status)


@router.get("", response_model=PlazaListResponse)
async def list_plazas(
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[PlazaStatus | None, Query(alias="status")] = None,
) -> PlazaListResponse:
    """최신순 plaza 카드 리스트. ``?status=`` 로 한 상태만 좁힐 수 있다.

    ``total`` 은 ``status`` 필터 적용 후 전체 개수 — 페이지네이션 위젯의 "총
    N건" 표시에 그대로. 같은 prefix 라우터의 ``""`` 라 ``/api/plazas`` 자체에
    매핑된다 (path 변수 라우트가 위로 가지 않게 등록 순서에 신경썼다 — FastAPI
    는 등록 순으로 매칭하지만 이 라우트는 path 충돌이 없어 사실상 안전).
    """
    store = _store(request)
    summaries, total = await store.list_plazas(
        limit=limit,
        offset=offset,
        status_filter=status_filter,
    )
    return PlazaListResponse(
        plazas=[
            PlazaSummaryItem(
                plaza_id=s.plaza_id,
                status=s.status,
                rounds_total=s.rounds_total,
                rounds_done=s.rounds_done,
                label=s.label,
                error=s.error,
                preset=s.preset,
                tokens_used=s.tokens_used,
                created_at=s.created_at,
                updated_at=s.updated_at,
            )
            for s in summaries
        ],
        total=total,
        limit=limit,
        offset=offset,
    )


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
            avatar_seed=_avatar_seed(profile.agent_id),
        )
        for profile in ontology.agents.values()
    ]
    return PlazaAgentsResponse(plaza_id=plaza_id, agents=agents)


@router.get("/{plaza_id}/layout", response_model=PlazaLayoutResponse)
async def get_layout(plaza_id: str, request: Request) -> PlazaLayoutResponse:
    """plaza 부감 뷰 (Plaza 화면) 용 노드 좌표 + 영향력.

    ``/agents`` 와 같은 200 + 게이팅 — pending / running 동안에도 200 으로
    떨어지지만 events.jsonl 이 안정적이지 않으므로 ``ready=False`` +
    ``agents=[]``. composing / completed / failed 는 ``ready=True`` 로 좌표
    + 영향력 채워서 돌려준다. events.jsonl 자체가 없어도 ontology_a 만 있으면
    엣지 0 으로 계산 (--fake 모드).

    좌표는 ``[0, 1] x [0, 1]`` 정규화. ``plaza_id`` 해시 시드라 같은 plaza 면
    리로드/폴링에서도 같은 값 — 프론트 캔버스에서 노드가 튀지 않는다.
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
    if record.status in {"pending", "running"}:
        # 부감 뷰는 sim 끝나야 의미 — 그동안엔 빈 응답.  ontology_a 손상 같은
        # 500/404 케이스는 status 가 composing 이상이 됐을 때 다시 검증된다.
        return PlazaLayoutResponse(plaza_id=plaza_id, ready=False, agents=[])

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

    if record.event_log_path is not None:
        edges, follower_counts = await asyncio.to_thread(
            _read_follow_edges, Path(record.event_log_path)
        )
    else:
        edges, follower_counts = [], {}

    profiles = list(ontology.agents.values())
    agent_ids = [p.agent_id for p in profiles]
    coords = await asyncio.to_thread(compute_layout, agent_ids, edges, seed=plaza_seed(plaza_id))
    max_follow = max(follower_counts.values(), default=0)
    items = [
        PlazaLayoutAgentItem(
            id=p.agent_id,
            name=p.name,
            role=p.entity_type,
            x=coords[p.agent_id][0],
            y=coords[p.agent_id][1],
            follower_count=follower_counts.get(p.agent_id, 0),
            influence=(follower_counts.get(p.agent_id, 0) / max_follow) if max_follow > 0 else 0.0,
            avatar_seed=_avatar_seed(p.agent_id),
        )
        for p in profiles
    ]
    return PlazaLayoutResponse(plaza_id=plaza_id, ready=True, agents=items)


@router.delete("/{plaza_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_plaza(plaza_id: str, request: Request) -> Response:
    """plaza 를 메모리·SQLite·디스크 산출물까지 통째로 정리.

    상태에 관계없이 받아들인다 — 잘못 만든 plaza 를 즉시 치우는 게 흔한 use
    case 라 ``pending`` / ``running`` / ``composing`` 도 cancel + cleanup 으로
    수렴시킨다. 자세한 동작은 ``PlazaStore.delete`` doc 참고. 없는 plaza 는 404.

    응답은 204 No Content — body 가 비므로 ``Response`` 를 직접 돌려준다
    (FastAPI 가 None 반환 시 4xx 가 아닌 한 빈 body 를 채워주긴 하지만, 명시적
    인 게 의도를 더 잘 드러낸다).
    """
    store = _store(request)
    deleted = await store.delete(plaza_id)
    if not deleted:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plaza {plaza_id!r} not found",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["router"]
