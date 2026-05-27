// =====================================================================
// 라이트미로 — App 라우터 + 셸
// 프로토타입의 hash 라우팅(app.jsx) → react-router-dom <Routes>.
// AppShell 은 현재 경로에서 currentScreen 을 파생하고, onNavigate 는
// useScreenNav 로 경로 이동을 수행한다. 랜딩 경로에서는 헤더를 숨긴다.
// BrowserRouter 는 main.tsx 에서 감싸므로 여기서는 사용하지 않는다.
// =====================================================================

import { useEffect, useMemo, useState } from 'react';
import { Routes, Route, Navigate, useLocation } from 'react-router-dom';
import { AppShell, type ScreenId } from '@/components/chrome';
import { useScreenNav } from '@/lib/nav';
import { api } from '@/api/client';

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

// URL pathname 에서 plazaId 분리 — `/live/abc123` 류 경로의 마지막 segment.
function plazaIdFromPath(pathname: string): string | null {
  const m = pathname.match(/^\/(casting|live|plaza|report)\/([^/?#]+)/);
  if (!m) return null;
  const id = m[2];
  // CastingReal 의 `/casting/new` 는 plaza_id 가 아니라 신규 흐름 sentinel.
  if (id === 'new') return null;
  return id;
}

const REPORT_REACHED_KEY = 'lm:reportReached';

export default function App() {
  const location = useLocation();
  const go = useScreenNav();
  const currentScreen = screenFromPath(location.pathname);
  const plazaId = useMemo(() => plazaIdFromPath(location.pathname), [location.pathname]);

  // 헤더에 표시할 광장 제목 — URL 의 plaza_id 로 /status 한 번 조회. label 이
  // null 이면 "광장 #abc12345" 식 단축 ID 로 폴백. plazaId 가 바뀔 때마다 재조회.
  const [plazaTitle, setPlazaTitle] = useState<string | null>(null);
  useEffect(() => {
    if (!plazaId) {
      setPlazaTitle(null);
      return;
    }
    let cancelled = false;
    api.getStatus(plazaId)
      .then((res) => {
        if (cancelled) return;
        setPlazaTitle(res.label ?? `광장 #${plazaId.slice(0, 8)}`);
      })
      .catch(() => {
        if (cancelled) return;
        setPlazaTitle(`광장 #${plazaId.slice(0, 8)}`);
      });
    return () => { cancelled = true; };
  }, [plazaId]);
  const plaza = plazaTitle ? { title: plazaTitle } : null;

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
