// =====================================================================
// 화면 네비게이션 헬퍼
// 프로토타입의 onNavigate(screenId) 를 React Router 경로로 매핑.
// DEMO_PLAZA_ID 는 직접 URL 진입(URL bar / 데모 링크) 폴백 — Seed 흐름에서
// 새로 만든 plaza 로 이동할 때는 pathForScreen(id, plazaId) 에 실 plaza_id 를
// 넘기거나 useScreenNav(plazaId) 로 초기화해서 사용한다.
// =====================================================================

import { useNavigate, useParams } from 'react-router-dom';
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
// 인자 미지정 시 현재 라우트의 :plazaId 를 따라간다 — Casting/Live/Plaza/Report
// 가 자기 URL 의 plaza_id 를 그대로 다음 화면에 전달하게 된다. 라우트 밖
// (e.g. Landing) 에서는 useParams 가 빈 객체라 DEMO_PLAZA_ID 폴백이 적용.
export function useScreenNav(plazaId?: string): (id: ScreenId) => void {
  const navigate = useNavigate();
  const { plazaId: routePlazaId } = useParams<{ plazaId: string }>();
  const effective = plazaId ?? routePlazaId ?? DEMO_PLAZA_ID;
  return (id: ScreenId) => navigate(pathForScreen(id, effective));
}
