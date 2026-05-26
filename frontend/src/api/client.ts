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
};
