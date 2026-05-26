// =====================================================================
// 화면 네비게이션 헬퍼
// 프로토타입의 onNavigate(screenId) 를 React Router 경로로 매핑.
// 데모는 단일 광장이므로 plazaId = 'demo' 고정.
// 실 제품에서는 POST /api/plazas 응답의 plaza_id 를 넘긴다.
// =====================================================================

import { useNavigate } from 'react-router-dom';
import type { ScreenId } from '@/components/chrome';

export const DEMO_PLAZA_ID = 'demo';

export function pathForScreen(id: ScreenId, plazaId: string = DEMO_PLAZA_ID): string {
  switch (id) {
    case 'landing':
      return '/';
    case 'seed':
      return '/seed';
    case 'casting':
      return `/casting/${plazaId}`;
    case 'live':
      return `/live/${plazaId}`;
    case 'plaza':
      return `/plaza/${plazaId}`;
    case 'report':
      return `/report/${plazaId}`;
    default:
      return '/';
  }
}

// 화면 컴포넌트에서: const go = useScreenNav();  go('plaza');
// 두 번째 인자로 plazaId 를 명시하면 그 plaza 로 — Seed → Casting 처럼
// `createPlaza` 응답의 실 plaza_id 로 이동할 때 쓴다. defaultPlazaId 는
// `useParams<{ plazaId: string }>().plazaId` 처럼 undefined 가능한 값을 그대로
// 받을 수 있게 optional + DEMO 폴백.
export function useScreenNav(
  defaultPlazaId?: string,
): (id: ScreenId, overridePlazaId?: string) => void {
  const navigate = useNavigate();
  const baseId = defaultPlazaId ?? DEMO_PLAZA_ID;
  return (id, overridePlazaId) =>
    navigate(pathForScreen(id, overridePlazaId ?? baseId));
}
