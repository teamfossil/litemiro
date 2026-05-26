"""API 요청/응답 모델 — Pydantic v2.

`PlazaStatus` literal 은 ``PlazaStore`` 의 상태 머신 (pending → running →
composing → completed | failed) 과 동기. 새 상태를 추가할 때는 store 와 함께
바꿔야 한다. ``composing`` 은 시뮬레이션은 끝났지만 LLM 보고서를 만드는 중간
구간 — terminal 아님, 프론트는 progress bar 100% 로 두고 "보고서 합성중"
표시.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from litemiro.phase1.models import Preset

PlazaStatus = Literal["pending", "running", "composing", "completed", "failed"]


class CreatePlazaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ontology_a_path: str = Field(min_length=1)
    ontology_b_path: str = Field(min_length=1)
    rounds: int = Field(ge=1, le=200)
    label: str | None = Field(default=None, max_length=120)
    # 보고서 합성 시 호출 수를 결정. quick=1 콜 / standard=4 콜 / full=8 콜.
    # 시뮬레이션 자체와는 직교 — sim 비용은 runner 설정이 본다.
    preset: Preset = Preset.QUICK


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


class PlazaAgentItem(BaseModel):
    """Casting 화면이 슬롯에 띄울 앵커 1명. ``OntologyA.agents`` 의 ``AgentProfile``
    에서 시각화에 의미 있는 필드만 추려 노출.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    # ``AgentProfile.entity_type`` 그대로 (raw). ontology 추출 결과의 카테고리
    # 라벨 — 프론트가 자체 매핑 테이블로 RoleId enum 으로 좁힌다.
    # 매핑 테이블 SSoT 는 ``docs/api/contract.md`` 의 ``/agents`` 섹션.
    role: str
    ideology: float = Field(ge=0.0, le=1.0)
    topics: list[str] = Field(default_factory=list)
    # agent_id 의 sha256 앞 4바이트 → uint32. 같은 plaza/같은 agent 면 reload·재연결에서도
    # 같은 값이 와서 프론트 deterministic avatar 가 안 튄다. 백엔드가 직접 계산하는 이유는
    # 프론트 해시 알고리즘 변경/언어 차이로 시드가 어긋나는 걸 막기 위해.
    avatar_seed: int = Field(ge=0, le=4294967295)


class PlazaAgentsResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plaza_id: str
    agents: list[PlazaAgentItem]


class PlazaLayoutAgentItem(BaseModel):
    """Plaza 부감 뷰의 노드 1개. ``compute_layout`` 의 결정적 좌표 + ontology_a
    의 시각화 메타 (name/role) + events.jsonl 누적의 영향력 (follower count).

    ``x`` / ``y`` 는 ``[0.0, 1.0]`` 정규화 — 프론트가 캔버스 크기 곱해 그린다.
    """

    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    role: str
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    # 같은 plaza 내 follower_count 최댓값으로 정규화한 [0.0, 1.0]. 노드 크기/색
    # 매핑에 그대로 곱해 쓴다. 최댓값 노드 = 1.0. 모두 0 follow 면 전부 0.0.
    influence: float = Field(ge=0.0, le=1.0)
    # 받은 follow 수 절대값 — 정규화 전 raw. 노드 호버 툴팁/디버깅 용.
    follower_count: int = Field(ge=0)
    # ``/agents`` 와 동일 알고리즘 (sha256(agent_id)[:4]). 두 응답이 같은 값.
    avatar_seed: int = Field(ge=0, le=4294967295)


class PlazaLayoutResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plaza_id: str
    # pending/running 동안만 False — events.jsonl 안정 안 됨 → ``agents=[]``.
    # composing/completed/failed 는 모두 True. 프론트는 ``ready`` 로 빈/채운
    # 부감 뷰를 분기. ``/agents`` 와 일관된 200 + 게이트 패턴.
    ready: bool
    # 좌표 박스. 현재 항상 1.0 x 1.0 — 향후 비정방 화면에 맞춰 늘릴 여지.
    width: float = 1.0
    height: float = 1.0
    agents: list[PlazaLayoutAgentItem]


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
    "PlazaAgentItem",
    "PlazaAgentsResponse",
    "PlazaLayoutAgentItem",
    "PlazaLayoutResponse",
    "PlazaReportResponse",
    "PlazaStatus",
    "PlazaStatusResponse",
]
