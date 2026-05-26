// =====================================================================
// 라이트미로 — App 라우터 + 셸
// 프로토타입의 hash 라우팅(app.jsx) → react-router-dom <Routes>.
// AppShell 은 현재 경로에서 currentScreen 을 파생하고, onNavigate 는
// useScreenNav 로 경로 이동을 수행한다. 랜딩 경로에서는 헤더를 숨긴다.
// BrowserRouter 는 main.tsx 에서 감싸므로 여기서는 사용하지 않는다.
// =====================================================================

import { useEffect, useState } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AppShell, type ScreenId } from '@/components/chrome';
import { useScreenNav } from '@/lib/nav';
import { lm } from '@/data/mock';

import Landing from '@/screens/Landing';
import Seed from '@/screens/Seed';
import Casting from '@/screens/Casting';
import Live from '@/screens/Live';
import Plaza from '@/screens/Plaza';
import Report from '@/screens/Report';
import { ApiStatusBadge } from '@/api/ApiStatusBadge';

// 경로 → 화면 ID 매핑 (헤더 활성 표시 + 헤더 숨김 판단용)
function screenFromPath(pathname: string): ScreenId {
  if (pathname.startsWith('/seed')) return 'seed';
  if (pathname.startsWith('/casting')) return 'casting';
  if (pathname.startsWith('/live')) return 'live';
  if (pathname.startsWith('/plaza')) return 'plaza';
  if (pathname.startsWith('/report')) return 'report';
  return 'landing';
}

const REPORT_REACHED_KEY = 'lm:reportReached';

export default function App() {
  const location = useLocation();
  const go = useScreenNav();
  const currentScreen = screenFromPath(location.pathname);
  // 광장 주제는 mock SEED 사용. 실제 광장 상태가 도입되면 store 에서 받는다.
  const plaza = { title: lm.SEED.title };

  // Landing 화면은 header 없이 전체 hero.
  const hideHeader = currentScreen === 'landing';

  // 리포트 도달 여부 — 세션 동안 유지. 도달 전까지 헤더 phase nav 잠금.
  const [reportReached, setReportReached] = useState<boolean>(
    () => typeof window !== 'undefined' && sessionStorage.getItem(REPORT_REACHED_KEY) === '1'
  );
  useEffect(() => {
    if (currentScreen === 'report' && !reportReached) {
      sessionStorage.setItem(REPORT_REACHED_KEY, '1');
      setReportReached(true);
    }
  }, [currentScreen, reportReached]);

  return (
    <AppShell
      plaza={plaza}
      currentScreen={currentScreen}
      onNavigate={go}
      hideHeader={hideHeader}
      phaseNavLocked={!reportReached}
    >
      <Routes>
        <Route path="/" element={<Landing />} />
        <Route path="/seed" element={<Seed />} />
        {/* Seed → /casting/new?ontology=... — ontology 폴링 + plaza 생성 단계.
            기존 /casting/:plazaId 는 Landing 의 데모 진입용으로 남겨둔다. */}
        <Route path="/casting/new" element={<Casting />} />
        <Route path="/casting/:plazaId" element={<Casting />} />
        <Route path="/live/:plazaId" element={<Live />} />
        <Route path="/plaza/:plazaId" element={<Plaza />} />
        <Route path="/report/:plazaId" element={<Report />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <ApiStatusBadge />
    </AppShell>
  );
}
