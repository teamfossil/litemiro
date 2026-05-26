"""``/api/documents`` — 사용자 자료 업로드 + 조회.

Phase 1 generation 의 입력이 될 PDF/TXT 한 건을 받아 디스크에 저장하고
``document_id`` 를 발급한다. 발급된 id 는 ``POST /api/ontologies`` 의 본문
``document_id`` 에 그대로 넣는다.

업로드는 ``multipart/form-data`` 한 번 — 라벨/추가 필드는 미지정. Phase 1
요구사항(``requirement``) 은 본 라우트가 아닌 ontology 라우트에서 받는다.
한 문서를 여러 ontology 가 재사용하는 시나리오를 막지 않으려는 분리.
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, File, HTTPException, Request, UploadFile, status

from litemiro.api.document_store import DocumentStore
from litemiro.api.models import DocumentListResponse, DocumentResponse

router = APIRouter(prefix="/api", tags=["documents"])

# 5 MB. PDF 1편/논문 수십건 정도는 들어가지만 Phase 1 LLM 콜 비용이 폭주하지
# 않게 상한. 더 큰 자료는 클라가 사전에 chunk/요약 후 보내야 한다.
MAX_UPLOAD_BYTES = 5 * 1024 * 1024

# 받기로 한 확장자만 허용. PDF 는 PyPDF2, TXT 는 그대로 — Phase 1 의
# ``_read_document`` 가 그 두 분기만 처리하므로 다른 확장자를 받아두면 나중에
# pipeline 단에서 raise 되는 게 끝이다.
_ALLOWED_EXTENSIONS: frozenset[str] = frozenset({".pdf", ".txt"})

_MIME_BY_EXTENSION: dict[str, str] = {
    ".pdf": "application/pdf",
    ".txt": "text/plain",
}


def _get_store(request: Request) -> DocumentStore:
    store = getattr(request.app.state, "document_store", None)
    if store is None:
        # create_app 에서 lifespan 으로 항상 채워야 하는데 빠진 경우 — 명확히 500.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="document_store not configured",
        )
    return store  # type: ignore[no-any-return]


@router.post(
    "/documents",
    response_model=DocumentResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_document(
    request: Request,
    file: Annotated[UploadFile, File(description="PDF 또는 TXT 한 건")],
) -> DocumentResponse:
    """Phase 1 입력으로 쓸 자료 한 건을 업로드. ``document_id`` 발급.

    크기 5 MB 초과 / 허용 확장자 외 / 빈 파일 / 파일명 누락은 422. 디스크
    저장 + sqlite row 생성 후 201 + 메타 응답.
    """
    raw_name = file.filename or ""
    if not raw_name:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="filename is required",
        )
    suffix = Path(raw_name).suffix.lower()
    if suffix not in _ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f"unsupported extension {suffix!r}, allowed: .pdf .txt",
        )
    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="empty file",
        )
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail=f"file too large ({len(content)} > {MAX_UPLOAD_BYTES})",
        )
    # 클라가 보낸 content_type 보다 확장자 기반 매핑을 신뢰. multipart 측이
    # application/octet-stream 으로 통일해 넘기는 경우가 많다.
    mime_type = _MIME_BY_EXTENSION[suffix]
    store = _get_store(request)
    row = store.upload(content=content, filename=raw_name, mime_type=mime_type)
    return DocumentResponse(
        document_id=row.document_id,
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        created_at=row.created_at,
    )


@router.get("/documents/{document_id}", response_model=DocumentResponse)
async def get_document(request: Request, document_id: str) -> DocumentResponse:
    store = _get_store(request)
    row = store.get(document_id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"document {document_id!r} not found",
        )
    return DocumentResponse(
        document_id=row.document_id,
        filename=row.filename,
        mime_type=row.mime_type,
        size_bytes=row.size_bytes,
        sha256=row.sha256,
        created_at=row.created_at,
    )


@router.get("/documents", response_model=DocumentListResponse)
async def list_documents(request: Request) -> DocumentListResponse:
    store = _get_store(request)
    rows = store.list()
    return DocumentListResponse(
        documents=[
            DocumentResponse(
                document_id=r.document_id,
                filename=r.filename,
                mime_type=r.mime_type,
                size_bytes=r.size_bytes,
                sha256=r.sha256,
                created_at=r.created_at,
            )
            for r in rows
        ]
    )


__all__ = ["MAX_UPLOAD_BYTES", "router"]
