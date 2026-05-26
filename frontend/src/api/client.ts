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

// 보고서 합성 호출 수 — quick=1 / standard=4 / full=8. 시뮬레이션 비용과는 직교.
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

export class ApiError extends Error {
  constructor(public readonly status: number, message: string) {
    super(message);
    this.name = 'ApiError';
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    headers: { 'content-type': 'application/json', ...(init?.headers ?? {}) },
    ...init,
  });
  if (!res.ok) {
    const text = await res.text().catch(() => '');
    throw new ApiError(res.status, text || res.statusText);
  }
  return (await res.json()) as T;
}

export const api = {
  health: () => request<HealthResponse>('/api/health'),
  createPlaza: (body: CreatePlazaRequest) =>
    request<CreatePlazaResponse>('/api/plazas', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
  getStatus: (plazaId: string) =>
    request<PlazaStatusResponse>(`/api/plazas/${encodeURIComponent(plazaId)}/status`),
  getReport: (plazaId: string) =>
    request<PlazaReportResponse>(`/api/plazas/${encodeURIComponent(plazaId)}/report`),
  getAgents: (plazaId: string) =>
    request<PlazaAgentsResponse>(`/api/plazas/${encodeURIComponent(plazaId)}/agents`),
  getLayout: (plazaId: string) =>
    request<PlazaLayoutResponse>(`/api/plazas/${encodeURIComponent(plazaId)}/layout`),

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

    es.addEventListener('progress', (ev) => {
      const data = JSON.parse((ev as MessageEvent).data) as PlazaProgressEvent;
      handlers.onProgress?.(data);
    });
    es.addEventListener('status', (ev) => {
      const data = JSON.parse((ev as MessageEvent).data) as PlazaStatusEvent;
      handlers.onStatus?.(data);
      if (data.status === 'completed' || data.status === 'failed') {
        close();
      }
    });
    es.addEventListener('action', (ev) => {
      const data = JSON.parse((ev as MessageEvent).data) as PlazaActionEvent;
      handlers.onAction?.(data);
    });
    es.addEventListener('actions_snapshot', (ev) => {
      const data = JSON.parse((ev as MessageEvent).data) as PlazaActionsSnapshotEvent;
      handlers.onActionsSnapshot?.(data);
    });
    if (handlers.onError) {
      es.onerror = handlers.onError;
    }

    return { close };
  },
};
