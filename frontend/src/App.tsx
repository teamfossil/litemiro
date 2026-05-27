// =====================================================================
// 라이트미로 — App 라우터 + 셸
// 프로토타입의 hash 라우팅(app.jsx) → react-router-dom <Routes>.
// AppShell 은 현재 경로에서 currentScreen 을 파생하고, onNavigate 는
// useScreenNav 로 경로 이동을 수행한다. 랜딩 경로에서는 헤더를 숨긴다.
// BrowserRouter 는 main.tsx 에서 감싸므로 여기서는 사용하지 않는다.
// =====================================================================

import { useEffect, useMemo, useState } from 'react';
import { Routes, Route, Navigate, useLocation, useNavigate } from 'react-router-dom';
import { AppShell, type ScreenId } from '@/components/chrome';
import { useScreenNav } from '@/lib/nav';
import { api } from '@/api/client';

import Landing from '@/screens/Landing';
import Seed from '@/screens/Seed';
import Casting from '@/screens/Casting';
import Live from '@/screens/Live';
import Plaza from '@/screens/Plaza';
import Report from '@/screens/Report';
import CastingDemoMock from '@/screens/demo/CastingDemoMock';
import LiveDemoMock from '@/screens/demo/LiveDemoMock';
import ReportDemoMock from '@/screens/demo/ReportDemoMock';
import { ApiStatusBadge } from '@/api/ApiStatusBadge';

// 경로 → 화면 ID 매핑 (헤더 활성 표시 + 헤더 숨김 판단용)
function screenFromPath(pathname: string): ScreenId {
  if (pathname.startsWith('/seed')) return 'seed';
  if (pathname.startsWith('/demo/casting') || pathname.startsWith('/casting')) return 'casting';
  if (pathname.startsWith('/demo/live') || pathname.startsWith('/live')) return 'live';
  if (pathname.startsWith('/demo/plaza') || pathname.startsWith('/plaza')) return 'plaza';
  if (pathname.startsWith('/demo/report') || pathname.startsWith('/report')) return 'report';
  return 'landing';
}

// URL pathname 에서 plazaId 분리 — `/live/abc123` 류 경로의 마지막 segment.
// /demo/* 경로는 plaza_id 가 없는 mock 데모이므로 null.
function plazaIdFromPath(pathname: string): string | null {
  if (pathname.startsWith('/demo/')) return null;
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
  const navigate = useNavigate();
  const currentScreen = screenFromPath(location.pathname);
  const plazaId = useMemo(() => plazaIdFromPath(location.pathname), [location.pathname]);
  const isDemo = location.pathname.startsWith('/demo/');

  // AppShell header phase-nav 가 호출하는 navigator. App 컴포넌트는 route 위에
  // 있어서 useParams 가 비어 useScreenNav() 의 폴백이 DEMO_PLAZA_ID 로 떨어진다 —
  // 사용자가 Report 에서 "광장" 탭 누르면 /plaza/demo 로 가버려 404 cascade.
  // plazaId 를 URL 에서 직접 추출해 명시적으로 넘긴다. /demo/* 경로는 /demo/{target}
  // 으로 라우팅해 production 흐름과 섞이지 않게 한다.
  const baseGo = useScreenNav(plazaId ?? undefined);
  const go = isDemo
    ? (id: ScreenId) => navigate(id === 'landing' ? '/' : `/demo/${id}`)
    : baseGo;

  // 헤더에 표시할 광장 제목 — 데모면 mock SEED 제목, 그 외엔 URL plaza_id 로
  // /status 한 번 조회. label 이 null 이면 "광장 #abc12345" 단축 ID 로 폴백.
  const [plazaTitle, setPlazaTitle] = useState<string | null>(null);
  useEffect(() => {
    if (isDemo) {
      // 동적 import 없이 mock 직접 — 동기. setState 한 번이면 충분.
      import('@/data/mock').then((m) => setPlazaTitle(m.lm.SEED.title));
      return;
    }
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
  }, [plazaId, isDemo]);
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
        {/* 데모 경로 — 백엔드 호출 없이 mock 데이터로만 시뮬레이션 시연. */}
        <Route path="/demo/casting" element={<CastingDemoMock />} />
        <Route path="/demo/live" element={<LiveDemoMock />} />
        <Route path="/demo/plaza" element={<Plaza />} />
        <Route path="/demo/report" element={<ReportDemoMock />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      <ApiStatusBadge />
    </AppShell>
  );
}
