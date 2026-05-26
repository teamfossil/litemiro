// =====================================================================
// useApiHealth — /api/health 한 번 호출해서 백엔드 살아있는지 표시.
// 폴링 안 함. 마운트 시 1 회만. 백엔드가 죽으면 ApiError 잡아서 null 로.
// =====================================================================

import { useEffect, useState } from 'react';
import { api, ApiError, type HealthResponse } from './client';

export type ApiHealthState =
  | { kind: 'loading' }
  | { kind: 'ok'; data: HealthResponse }
  | { kind: 'down'; reason: string };

export function useApiHealth(): ApiHealthState {
  const [state, setState] = useState<ApiHealthState>({ kind: 'loading' });
  useEffect(() => {
    let cancelled = false;
    api
      .health()
      .then((data) => {
        if (!cancelled) setState({ kind: 'ok', data });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const reason =
          err instanceof ApiError ? `${err.status} ${err.message}` : (err as Error).message;
        setState({ kind: 'down', reason });
      });
    return () => {
      cancelled = true;
    };
  }, []);
  return state;
}
