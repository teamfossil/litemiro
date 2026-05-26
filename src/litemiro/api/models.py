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
    """완료된 plaza 의 보고서 응답 — 결정적 집계 + (선택) LLM Markdown 본문.

    step 4 에서 ``report_markdown`` 이 합류했다. composer 가 안 붙은 fake
    서버나 Opus+Qwen 동시 사망 폴백 케이스에는 ``None`` — 클라이언트는
    통계만 렌더하고 자연어 본문은 비운다.
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
    report_markdown: str | None = None
    report_fallback_used: bool = False


__all__ = [
    "CreatePlazaRequest",
    "CreatePlazaResponse",
    "HealthResponse",
    "PlazaReportResponse",
    "PlazaStatus",
    "PlazaStatusResponse",
]
