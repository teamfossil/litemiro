"""API 요청/응답 모델 — Pydantic v2.

`PlazaStatus` literal 은 ``PlazaStore`` 의 상태 머신 (pending → running →
composing → completed | failed) 과 동기. 새 상태를 추가할 때는 store 와 함께
바꿔야 한다. ``composing`` 은 시뮬레이션은 끝났지만 LLM 보고서를 만드는 중간
구간 — terminal 아님, 프론트는 progress bar 100% 로 두고 "보고서 합성중"
표시.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from litemiro.phase1.models import Preset

PlazaStatus = Literal["pending", "running", "composing", "completed", "failed"]
OntologyStatus = Literal["pending", "running", "completed", "failed"]


class CreatePlazaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 두 경로 모두 optional — 프론트 Seed 화면은 자료 업로드 UI 가 없어 항상
    # 같은 sample 로 호출하므로 path 박는 게 어색하다. 생략하면 라우트가
    # ``sample_fixtures.DEFAULT_ONTOLOGY_*_PATH`` (repo 의 dev fixture) 로 채운다.
    # 빈 문자열은 막아둔다 — JSON 직렬화 사고로 ``""`` 가 들어오는 경우 default
    # 폴백 의도와 어긋나서 헷갈리니, 명시한 거면 길이 1 이상이어야 한다.
    ontology_a_path: str | None = Field(default=None, min_length=1)
    ontology_b_path: str | None = Field(default=None, min_length=1)
    # /api/ontologies 로 생성한 결과를 그대로 plaza 에 연결하는 경로. 명시되면
    # ``ontology_a_path/b_path`` 보다 우선하며 dev fixture 폴백도 무시한다 —
    # 사용자 PDF 가 실제로 시뮬에 반영되는 정공 경로.
    ontology_id: str | None = Field(default=None, min_length=1)
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


class PlazaSummaryItem(BaseModel):
    """``GET /api/plazas`` 목록 한 줄. ``PlazaStatusResponse`` 와 같은 진행
    상태 필드 + ``preset`` / ``tokens_used`` / 두 timestamp 까지. ``report_markdown``
    같은 큰 본문은 일부러 빼서 카드 리스트가 가볍게 그려지게 한다.
    """

    model_config = ConfigDict(extra="forbid")

    plaza_id: str
    status: PlazaStatus
    rounds_total: int
    rounds_done: int
    label: str | None = None
    error: str | None = None
    preset: Preset
    tokens_used: int
    created_at: datetime
    updated_at: datetime


class PlazaListResponse(BaseModel):
    """``GET /api/plazas`` 의 응답.

    ``total`` 은 필터(``status``) 가 걸린 경우 그 필터 후 전체 row 수 — 페이지
    네이션 위젯의 "총 N건" 표시에 그대로 쓸 수 있게 한다. ``plazas`` 자체는
    ``limit`` / ``offset`` 또는 ``cursor`` 로 잘려 들어온 한 페이지.

    ``next_cursor`` 는 다음 페이지가 있을 가능성이 있으면 opaque 문자열, 마지막
    페이지면 ``None``. offset 모드 응답에도 채워서 클라가 첫 호출(no cursor)
    이후 두 번째부터 cursor 로 갈아탈 수 있게 한다 (infinite scroll 패턴).
    한 페이지가 정확히 ``limit`` 만큼 차고 그게 끝이면 한 번 더 호출해 빈
    페이지를 받고 끝을 확인하는 게 keyset 의 정석.
    """

    model_config = ConfigDict(extra="forbid")

    plazas: list[PlazaSummaryItem]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=100)
    offset: int = Field(ge=0)
    next_cursor: str | None = None


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


class DocumentResponse(BaseModel):
    """업로드된 사용자 문서 한 건. ``storage_path`` 는 디스크 위치라 외부 비공개 —
    응답에선 메타데이터(파일명/MIME/크기/sha256/document_id) 만 노출.
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str
    filename: str
    mime_type: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(min_length=64, max_length=64)
    created_at: datetime


class DocumentListResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    documents: list[DocumentResponse]


class CreateOntologyRequest(BaseModel):
    """``POST /api/ontologies`` 본문 — 어떤 문서로 어떤 규모/조건의 ontology 를
    만들지. ``requirement`` 는 Phase 1 의 entity ranking / profile generation 에
    그대로 전달되는 한 줄 문맥 (예: "아이스라엘 갈등에 대한 시민 반응 시뮬").
    """

    model_config = ConfigDict(extra="forbid")

    document_id: str = Field(min_length=1)
    requirement: str = Field(min_length=1, max_length=500)
    preset: Preset = Preset.QUICK


class OntologyResponse(BaseModel):
    """Phase 1 generation 한 건의 상태. ``status='completed'`` 이면 ``ready=True``
    + ``agent_count`` 채워짐. 그 외에는 ``ready=False`` 로 두고 프론트는 폴링.
    """

    model_config = ConfigDict(extra="forbid")

    ontology_id: str
    document_id: str
    status: OntologyStatus
    preset: Preset
    requirement: str
    agent_count: int | None = None
    error: str | None = None
    # status == 'completed' 의 단순 별칭. 폴링 측이 boolean 한 줄로 분기할 수
    # 있도록 노출 — plaza 의 ``ready`` 패턴과 같은 의도.
    ready: bool
    created_at: datetime
    updated_at: datetime


__all__ = [
    "CreateOntologyRequest",
    "CreatePlazaRequest",
    "CreatePlazaResponse",
    "DocumentListResponse",
    "DocumentResponse",
    "HealthResponse",
    "OntologyResponse",
    "OntologyStatus",
    "PlazaAgentItem",
    "PlazaAgentsResponse",
    "PlazaLayoutAgentItem",
    "PlazaLayoutResponse",
    "PlazaListResponse",
    "PlazaReportResponse",
    "PlazaStatus",
    "PlazaStatusResponse",
    "PlazaSummaryItem",
]
