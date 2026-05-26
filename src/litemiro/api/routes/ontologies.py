"""``/api/ontologies`` — Phase 1 generation 트리거 + 폴링.

``POST /api/ontologies`` 본문에 ``{document_id, preset, requirement}`` 가
들어오면 ``OntologyStore.generate`` 가 ontology_id 발급 + 백그라운드 task
시작. 클라는 ``GET /api/ontologies/{id}`` 로 폴링해 ``ready=true`` 가 되면
``POST /api/plazas`` 의 ``ontology_id`` 에 같은 id 를 넘긴다 — 사용자 PDF
가 실제 시뮬에 반영되는 3-step 정공 흐름.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status

from litemiro.api.db import OntologyRow
from litemiro.api.document_store import DocumentStore
from litemiro.api.models import CreateOntologyRequest, OntologyResponse
from litemiro.api.ontology_store import OntologyStore

router = APIRouter(prefix="/api", tags=["ontologies"])


def _get_stores(request: Request) -> tuple[DocumentStore, OntologyStore]:
    document_store = getattr(request.app.state, "document_store", None)
    ontology_store = getattr(request.app.state, "ontology_store", None)
    if document_store is None or ontology_store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="document_store / ontology_store not configured",
        )
    return document_store, ontology_store


def _to_response(row: OntologyRow) -> OntologyResponse:
    return OntologyResponse(
        ontology_id=row.ontology_id,
        document_id=row.document_id,
        status=row.status,
        preset=row.preset,
        requirement=row.requirement,
        agent_count=row.agent_count,
        error=row.error,
        ready=row.status == "completed",
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.post(
    "/ontologies",
    response_model=OntologyResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_ontology(request: Request, body: CreateOntologyRequest) -> OntologyResponse:
    """Phase 1 generation 한 건 시작. 202 + ``status='pending'`` 으로 즉시 응답.

    실제 LLM 콜은 백그라운드. 클라는 ``ontology_id`` 로 폴링.
    """
    document_store, ontology_store = _get_stores(request)
    doc = document_store.get(body.document_id)
    if doc is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document {body.document_id!r} not found",
        )
    row = ontology_store.generate(
        document_id=body.document_id,
        document_path=doc.storage_path,
        preset=body.preset,
        requirement=body.requirement,
    )
    return _to_response(row)


@router.get("/ontologies/{ontology_id}", response_model=OntologyResponse)
async def get_ontology(request: Request, ontology_id: str) -> OntologyResponse:
    _, ontology_store = _get_stores(request)
    row = ontology_store.get(ontology_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"ontology {ontology_id!r} not found",
        )
    return _to_response(row)


__all__ = ["router"]
