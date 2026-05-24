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
export function useScreenNav(plazaId: string = DEMO_PLAZA_ID): (id: ScreenId) => void {
  const navigate = useNavigate();
  return (id: ScreenId) => navigate(pathForScreen(id, plazaId));
}
