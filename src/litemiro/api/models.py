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


__all__ = [
    "CreatePlazaRequest",
    "CreatePlazaResponse",
    "HealthResponse",
    "PlazaStatus",
    "PlazaStatusResponse",
]
