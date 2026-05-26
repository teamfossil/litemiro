// =====================================================================
// 라이트미로 — 백엔드 HTTP 클라이언트
// litemiro-api (FastAPI) 가 노출하는 /api/* 엔드포인트의 얇은 wrapper.
// 백엔드 Pydantic 스키마와 1:1 미러 — 변경 시 양쪽 같이 손볼 것.
// 개발은 Vite proxy(`/api` → localhost:8765) 로 동일 origin 가정.
// 배포는 VITE_API_BASE 환경변수로 절대 origin 주입.
// =====================================================================

const API_BASE: string = import.meta.env.VITE_API_BASE ?? '';

export type PlazaStatus = 'pending' | 'running' | 'completed' | 'failed';

export interface HealthResponse {
  status: 'ok';
  version: string;
}

export interface CreatePlazaRequest {
  ontology_a_path: string;
  ontology_b_path: string;
  rounds: number;
  label?: string;
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
    if (handlers.onError) {
      es.onerror = handlers.onError;
    }

    return { close };
  },
};
