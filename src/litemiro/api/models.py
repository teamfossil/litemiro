"""API 요청/응답 모델 — Pydantic v2.

`PlazaStatus` literal 은 ``PlazaStore`` 의 상태 머신 (pending → running →
completed | failed) 과 동기. 새 상태를 추가할 때는 store 와 함께 바꿔야 한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

PlazaStatus = Literal["pending", "running", "completed", "failed"]


class CreatePlazaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ontology_a_path: str = Field(min_length=1)
    ontology_b_path: str = Field(min_length=1)
    rounds: int = Field(ge=1, le=200)
    label: str | None = Field(default=None, max_length=120)


class CreatePlazaResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plaza_id: str
    status: PlazaStatus


class PlazaStatusResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plaza_id: str
    status: PlazaStatus
    rounds_total: int
    rounds_done: int
    label: str | None = None
    error: str | None = None


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["ok"] = "ok"
    version: str


class PlazaReportResponse(BaseModel):
    """step 2 보고서 응답 — 결정적 집계만. LLM 인사이트는 step 4 에서 채운다.

    프론트엔드는 본 응답 + Phase 1 ontology(앵커 정보) 를 가지고 화면용
    ``ReportData`` 를 합성한다. 본 단계에서는 LLM-derived 필드 (prediction
    headline, topic stance 등) 는 의도적으로 비워둔다 — 모의 데이터로 채우면
    사용자가 진짜 결과로 오해할 수 있음.
    """

    model_config = ConfigDict(extra="forbid")

    plaza_id: str
    label: str | None
    status: PlazaStatus
    rounds_total: int
    rounds_done: int
    tokens_used: int
    n_events: int
    n_agents: int
    n_rounds: int
    # AggregationResult.categories 의 카테고리 → 자유 dict
    categories: dict[str, dict[str, object]]
    qa_metrics: dict[str, float]


__all__ = [
    "CreatePlazaRequest",
    "CreatePlazaResponse",
    "HealthResponse",
    "PlazaReportResponse",
    "PlazaStatus",
    "PlazaStatusResponse",
]
