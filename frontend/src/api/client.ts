// =====================================================================
// 라이트미로 — 백엔드 HTTP 클라이언트
// litemiro-api (FastAPI) 가 노출하는 /api/* 엔드포인트의 얇은 wrapper.
// 백엔드 Pydantic 스키마와 1:1 미러 — 변경 시 양쪽 같이 손볼 것.
// 개발은 Vite proxy(`/api` → localhost:8765) 로 동일 origin 가정.
// 배포는 VITE_API_BASE 환경변수로 절대 origin 주입.
// =====================================================================

const API_BASE: string = import.meta.env.VITE_API_BASE ?? '';

// `composing` 은 시뮬레이션은 끝났지만 LLM 보고서 합성 중인 중간 단계.
// terminal 아님 — progress bar 100% 로 두고 "보고서 합성중" 표시용.
export type PlazaStatus = 'pending' | 'running' | 'composing' | 'completed' | 'failed';

// ontology generation 의 상태 — plaza 와 달리 composing 단계가 없다.
export type OntologyStatus = 'pending' | 'running' | 'completed' | 'failed';

// 두 가지를 한꺼번에 결정 — (a) 보고서 합성 LLM 콜 수 (quick=1 / standard=4 /
// full=8), (b) `POST /api/ontologies` 의 ontology agent 수 (quick=100 /
// standard=300 / full=500). 자세한 의미는 docs/api/contract.md L59.
export type Preset = 'quick' | 'standard' | 'full';

export interface HealthResponse {
  status: 'ok';
  version: string;
}

// 액션 타입 — 백엔드 ActionType enum 미러. DO_NOTHING 은 SSE 단계에서 컷되므로
// 클라이언트가 받는 액션은 항상 의미 있는 5종 중 하나.
export type ActionType = 'CREATE_POST' | 'LIKE_POST' | 'REPOST' | 'QUOTE_POST' | 'FOLLOW';

export interface CreatePlazaRequest {
  // 둘 다 optional — 미지정 시 백엔드가 dev fixture 로 폴백 (#88). 프론트 Seed
  // 화면은 자료 업로드 UI 가 없어 항상 같은 sample 호출이라 path 박지 않는다.
  ontology_a_path?: string;
  ontology_b_path?: string;
  // /api/ontologies 로 만든 결과를 그대로 연결하는 정공 경로. 명시되면
  // ontology_*_path 보다 우선하고 dev fixture 폴백도 무시된다.
  ontology_id?: string;
  rounds: number;
  label?: string;
  // 미지정 시 백엔드가 quick 으로 채움 (CreatePlazaRequest.preset default).
  preset?: Preset;
}

export interface CreatePlazaResponse {
  plaza_id: string;
  status: PlazaStatus;
}

export interface PlazaStatusResponse {
  plaza_id: string;
  status: PlazaStatus;
  rounds_total: number;
  rounds_done: number;
  label: string | null;
  error: string | null;
}

// `GET /api/plazas` 목록 한 줄. `report_markdown` 같은 큰 본문이 빠져 있어
// 카드 리스트 그리기 가볍다. 백엔드 `PlazaSummaryItem` 과 1:1.
export interface PlazaSummaryItem {
  plaza_id: string;
  status: PlazaStatus;
  rounds_total: number;
  rounds_done: number;
  label: string | null;
  error: string | null;
  preset: Preset;
  tokens_used: number;
  // ISO 8601.
  created_at: string;
  updated_at: string;
}

// `GET /api/plazas` 응답. `next_cursor` 가 채워져 있으면 다음 페이지 있을
// 가능성, `null` 이면 마지막. 첫 호출은 cursor 없이 보내고 두 번째 호출부터
// `cursor=next_cursor` 로 keyset 모드 갈아탈 수 있다.
export interface PlazaListResponse {
  plazas: PlazaSummaryItem[];
  total: number;
  limit: number;
  offset: number;
  next_cursor: string | null;
}

export interface ListPlazasParams {
  limit?: number;
  cursor?: string;
  status?: PlazaStatus;
}

// SSE — /api/plazas/{id}/events 가 흘려보내는 두 종의 페이로드.
// 백엔드 routes/events.py 와 1:1 미러.
export interface PlazaProgressEvent {
  rounds_done: number;
  rounds_total: number;
}

export interface PlazaStatusEvent {
  status: PlazaStatus;
  rounds_done: number;
  rounds_total: number;
  error: string | null;
}

export interface PlazaEventHandlers {
  onProgress?: (event: PlazaProgressEvent) => void;
  onStatus?: (event: PlazaStatusEvent) => void;
  // 라이브 액션 1건. events.jsonl 의 한 줄 (DO_NOTHING 제외) 이 push 된다.
  onAction?: (event: PlazaActionEvent) => void;
  // 연결 직후 한 번. 재연결/탭 복귀 후 빈 피드로 시작하지 않게 최근 40건을
  // 시간 오름차순으로 한 번에 흘려준다. 액션 0건이면 본 콜백 호출 안 됨.
  onActionsSnapshot?: (event: PlazaActionsSnapshotEvent) => void;
  // EventSource 의 raw error — 네트워크 끊김/타임아웃 등. 핸들러가 없으면 무시.
  onError?: (event: Event) => void;
}

export interface PlazaEventStream {
  close: () => void;
}

export interface PlazaReportResponse {
  plaza_id: string;
  label: string | null;
  status: PlazaStatus;
  rounds_total: number;
  rounds_done: number;
  tokens_used: number;
  n_events: number;
  n_agents: number;
  n_rounds: number;
  // 백엔드의 AggregationResult.categories — 카테고리별 자유 dict
  categories: Record<string, Record<string, unknown>>;
  qa_metrics: Record<string, number>;
  // step 4 — LLM ReportComposer 가 채운 Markdown 본문.
  // composer 가 안 붙은 fake 서버나 Opus+Qwen 동시 사망 폴백에서는 null.
  report_markdown: string | null;
  report_fallback_used: boolean;
}

// --------------------------------------------------------------------
// /agents — Casting 화면 슬롯용. plaza pending/running 단계에서도 200.
// 백엔드 PlazaAgentItem / PlazaAgentsResponse 와 1:1.
// --------------------------------------------------------------------
export interface PlazaAgentItem {
  id: string;
  name: string;
  // AgentProfile.entity_type raw 값. 프론트가 docs/api/contract.md 의 매핑 표로
  // RoleId enum 으로 좁힌다 (예: AIRegulationPolicy → policy).
  role: string;
  // 0.0 = 진보 / 1.0 = 보수 (Phase 1 ontology 정의). Casting position bar 의
  // 시각 라벨과 의미가 다르므로 화면 단에서 라벨 갱신 필요.
  ideology: number;
  topics: string[];
  // sha256(agent_id)[:4] 의 uint32. reload·재연결에서도 동일 — 프론트
  // deterministic 아바타 생성 시드.
  avatar_seed: number;
}

export interface PlazaAgentsResponse {
  plaza_id: string;
  agents: PlazaAgentItem[];
}

// --------------------------------------------------------------------
// /layout — Plaza 부감 뷰 노드 좌표. pending/running 도 200 으로 떨어지되
// 그땐 ready=false + agents=[]. composing 이후엔 ready=true + 좌표 채움.
// 백엔드 PlazaLayoutAgentItem / PlazaLayoutResponse 와 1:1.
// --------------------------------------------------------------------
export interface PlazaLayoutAgentItem {
  id: string;
  name: string;
  role: string;
  // 좌표 박스 [0, 1] x [0, 1] 안의 정규화 값. 프론트가 캔버스 크기만 곱한다.
  x: number;
  y: number;
  // 0.0 ~ 1.0 normalized. 노드 크기/색 매핑에 그대로 사용.
  influence: number;
  // 받은 FOLLOW 개수 raw. tooltip / 정렬에 활용.
  follower_count: number;
  avatar_seed: number;
}

export interface PlazaLayoutResponse {
  plaza_id: string;
  // false 면 agents 가 [] — events.jsonl 이 안정화되지 않아 좌표 계산 불가
  // (pending/running). 프론트는 "아직 부감 데이터 없음" UI 노출.
  ready: boolean;
  width: number;
  height: number;
  agents: PlazaLayoutAgentItem[];
}

// --------------------------------------------------------------------
// SSE — 액션 이벤트 (event: action / event: actions_snapshot).
// 백엔드 events.jsonl 라이브 tail. DO_NOTHING 은 백엔드가 컷.
// --------------------------------------------------------------------
export interface PlazaActionEvent {
  round_num: number;
  agent_id: string;
  type: ActionType;
  target_post_id: string | null;
  target_agent_id: string | null;
  content: string | null;
  // ISO 8601 timestamp.
  timestamp: string;
}

// 연결 직후 한 번 — 최근 40건 (시간 오름차순). 액션 0건이면 본 이벤트 자체가
// 생략된다.
export interface PlazaActionsSnapshotEvent {
  actions: PlazaActionEvent[];
}

// --------------------------------------------------------------------
// /documents — 사용자 PDF/TXT 업로드. multipart/form-data 1회로 끝낸다.
// 백엔드 DocumentResponse / DocumentListResponse 와 1:1 미러.
// --------------------------------------------------------------------
export interface DocumentResponse {
  document_id: string;
  filename: string;
  mime_type: string;
  size_bytes: number;
  // 64자 hex — 동일 파일 재업로드 식별용.
  sha256: string;
  // ISO 8601 timestamp.
  created_at: string;
}

export interface DocumentListResponse {
  documents: DocumentResponse[];
}

// --------------------------------------------------------------------
// /ontologies — Phase 1 generation. POST 즉시 202 + ontology_id, 클라는
// GET 폴링으로 ready=true 까지 대기. 백엔드 CreateOntologyRequest /
// OntologyResponse 와 1:1.
// --------------------------------------------------------------------
export interface CreateOntologyRequest {
  document_id: string;
  // Phase 1 ranking/profile generation 에 그대로 들어가는 한 줄 문맥
  // (예: "주 4일제 도입에 대한 시민 반응 시뮬"). 1~500자.
  requirement: string;
  preset?: Preset;
}

export interface OntologyResponse {
  ontology_id: string;
  document_id: string;
  status: OntologyStatus;
  preset: Preset;
  requirement: string;
  // status=completed 인 경우에만 채워짐.
  agent_count: number | null;
  error: string | null;
  // status === 'completed' 의 단순 별칭. 폴링 측이 boolean 한 줄로 분기.
  ready: boolean;
  created_at: string;
  updated_at: string;
}

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit, signal?: AbortSignal): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    signal,
    ...init,
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new ApiError(res.status, text || res.statusText);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>('/api/health'),

  /**
   * PDF/TXT 한 건 업로드. ``content-type`` 헤더는 일부러 비운다 —
   * 브라우저가 multipart boundary 까지 포함해 자동으로 채우게 두는 게 정공.
   * 직접 application/json 으로 박으면 422 가 떨어진다.
   */
  uploadDocument: async (file: File, signal?: AbortSignal): Promise<DocumentResponse> => {
    const form = new FormData();
    form.append('file', file);
    const res = await fetch(`${API_BASE}/api/documents`, {
      method: 'POST',
      body: form,
      signal,
    });
    if (!res.ok) {
      const text = await res.text().catch(() => '');
      throw new ApiError(res.status, text || res.statusText);
    }
    return (await res.json()) as DocumentResponse;
  },
  getDocument: (documentId: string, signal?: AbortSignal) =>
    request<DocumentResponse>(`/api/documents/${encodeURIComponent(documentId)}`, undefined, signal),

  createOntology: (body: CreateOntologyRequest) =>
    request<OntologyResponse>('/api/ontologies', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  getOntology: (ontologyId: string, signal?: AbortSignal) =>
    request<OntologyResponse>(`/api/ontologies/${encodeURIComponent(ontologyId)}`, undefined, signal),

  createPlaza: (body: CreatePlazaRequest) =>
    request<CreatePlazaResponse>('/api/plazas', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  listPlazas: (params?: ListPlazasParams) => {
    const search = new URLSearchParams();
    if (params?.limit !== undefined) search.set('limit', String(params.limit));
    if (params?.cursor !== undefined) search.set('cursor', params.cursor);
    if (params?.status !== undefined) search.set('status', params.status);
    const q = search.toString();
    return request<PlazaListResponse>(`/api/plazas${q ? `?${q}` : ''}`);
  },
  getStatus: (plazaId: string, signal?: AbortSignal) =>
    request<PlazaStatusResponse>(`/api/plazas/${encodeURIComponent(plazaId)}/status`, undefined, signal),
  getReport: (plazaId: string, signal?: AbortSignal) =>
    request<PlazaReportResponse>(`/api/plazas/${encodeURIComponent(plazaId)}/report`, undefined, signal),
  getAgents: (plazaId: string, signal?: AbortSignal) =>
    request<PlazaAgentsResponse>(`/api/plazas/${encodeURIComponent(plazaId)}/agents`, undefined, signal),
  getLayout: (plazaId: string, signal?: AbortSignal) =>
    request<PlazaLayoutResponse>(`/api/plazas/${encodeURIComponent(plazaId)}/layout`, undefined, signal),

  /**
   * `/status` 폴링 대신 SSE 로 progress / status 이벤트를 push 받는다.
   *
   * status 가 `completed` | `failed` 로 들어오면 백엔드도 스트림을 닫고
   * 본 헬퍼는 EventSource 를 자동으로 close 한다. 호출자가 그 전에
   * 화면을 떠나면 반환값의 `close()` 로 명시 정리.
   *
   * EventSource 는 브라우저 표준 — Node 환경에서는 폴리필이 필요하다.
   * 단위 테스트는 별도 mock 가 들어간다.
   */
  streamPlazaEvents(plazaId: string, handlers: PlazaEventHandlers): PlazaEventStream {
    const url = `${API_BASE}/api/plazas/${encodeURIComponent(plazaId)}/events`;
    const es = new EventSource(url);
    const close = () => es.close();

    // 재연결 중 빈/불완전 페이로드나 malformed JSON 이 한 번 들어와도 스트림 전체가
    // 죽으면 안 된다. parse 실패 이벤트는 버리되 EventSource 는 그대로 둔다 —
    // 다음 정상 이벤트로 자연히 복구된다. 실패는 onError 로만 흘려 통지.
    const parseEvent = <T>(ev: Event): T | null => {
      try {
        return JSON.parse((ev as MessageEvent).data) as T;
      } catch (err) {
        handlers.onError?.(err instanceof Event ? err : ev);
        return null;
      }
    };

    es.addEventListener('progress', (ev) => {
      const data = parseEvent<PlazaProgressEvent>(ev);
      if (data) handlers.onProgress?.(data);
    });
    es.addEventListener('status', (ev) => {
      const data = parseEvent<PlazaStatusEvent>(ev);
      if (!data) return;
      handlers.onStatus?.(data);
      if (data.status === 'completed' || data.status === 'failed') {
        close();
      }
    });
    es.addEventListener('action', (ev) => {
      const data = parseEvent<PlazaActionEvent>(ev);
      if (data) handlers.onAction?.(data);
    });
    es.addEventListener('actions_snapshot', (ev) => {
      const data = parseEvent<PlazaActionsSnapshotEvent>(ev);
      if (data) handlers.onActionsSnapshot?.(data);
    });
    if (handlers.onError) {
      es.onerror = handlers.onError;
    }

    return { close };
  },
};
