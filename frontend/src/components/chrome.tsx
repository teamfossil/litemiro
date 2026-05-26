// =====================================================================
// 라이트미로 — 프레임 컴포넌트
// Header, NavBar, AppShell. 모든 화면이 이 셸 안에서 산다.
// (chrome.jsx → ES 모듈 + 타입. 라우팅은 App이 onNavigate로 주입.)
// =====================================================================

import type { ReactNode } from 'react';

export type ScreenId = 'landing' | 'seed' | 'casting' | 'live' | 'plaza' | 'report';

export interface PlazaMeta {
  title: string;
}

// --------------------------------------------------------------------
// BrandMark — 좌상단. 3색 도트 + 워드마크.
// --------------------------------------------------------------------
export function BrandMark({ size = 'md' }: { size?: 'md' | 'lg' }) {
  const dotSize = size === 'lg' ? 12 : 10;
  return (
    <div className={`lm-brand lm-brand--${size}`}>
      <div className="lm-brand__dots" aria-hidden="true">
        <span style={{ width: dotSize, height: dotSize, background: 'var(--ink)' }} />
        <span style={{ width: dotSize, height: dotSize, background: 'var(--r-broadcast)' }} />
        <span style={{ width: dotSize, height: dotSize, background: 'var(--r-citizen-p)' }} />
        <span style={{ width: dotSize, height: dotSize, background: 'var(--r-academic)' }} />
      </div>
      <span className="lm-brand__word">LiteMiro</span>
    </div>
  );
}

// --------------------------------------------------------------------
// AppHeader — 모든 인-앱 화면의 상단.
// --------------------------------------------------------------------
export interface AppHeaderProps {
  plaza?: PlazaMeta | null;
  currentScreen: string;
  onNavigate: (id: ScreenId) => void;
  // 리포트 도착 전에는 현재 화면 외 phase nav 잠금.
  phaseNavLocked?: boolean;
}

export function AppHeader({ plaza, currentScreen, onNavigate, phaseNavLocked = false }: AppHeaderProps) {
  // 공용 헤더는 캐스팅부터 — 시드는 SeedHeader 사용. phase 번호는 캐스팅=01 부터.
  const phaseScreens: { id: ScreenId; label: string; phase: number }[] = [
    { id: 'casting', label: '캐스팅', phase: 1 },
    { id: 'live', label: '진행', phase: 2 },
    { id: 'plaza', label: '광장', phase: 3 },
    { id: 'report', label: '리포트', phase: 4 },
  ];

  return (
    <header className="lm-header">
      <div className="lm-header__left">
        <button type="button" className="lm-header__brand" onClick={() => onNavigate('landing')}>
          <BrandMark size="lg" />
        </button>
        {plaza && (
          <div className="lm-header__plaza">
            <span className="lm-header__plaza-tag">광장</span>
            <span className="lm-header__plaza-title">{plaza.title}</span>
          </div>
        )}
      </div>

      <nav className="lm-header__nav" role="navigation">
        {phaseScreens.map((s) => {
          const isActive = currentScreen === s.id;
          const isLocked = phaseNavLocked && !isActive;
          return (
            <button
              key={s.id}
              type="button"
              onClick={() => {
                if (!isLocked) onNavigate(s.id);
              }}
              disabled={isLocked}
              aria-disabled={isLocked}
              title={isLocked ? '리포트 단계에 도착하면 이동할 수 있어요.' : undefined}
              className={`lm-header__navitem${isActive ? ' is-active' : ''}${isLocked ? ' is-locked' : ''}`}
            >
              <span className="lm-header__navnum">{String(s.phase).padStart(2, '0')}</span>
              <span className="lm-header__navlabel">{s.label}</span>
            </button>
          );
        })}
      </nav>

      <div className="lm-header__right">
        <button type="button" className="lm-header__iconbtn" aria-label="알림">
          <BellIcon />
        </button>
        <button type="button" className="lm-header__avatar" aria-label="내 계정">
          <div className="lm-header__avatar-dot" style={{ background: 'var(--r-citizen-p)' }} />
        </button>
      </div>
    </header>
  );
}

// --------------------------------------------------------------------
// SeedHeader — 시드 단계 전용. brand + 중앙 "시드 · 자료 입력 · 결제" 라벨 + 우측 아이콘.
// --------------------------------------------------------------------
export function SeedHeader({ onNavigate }: { onNavigate: (id: ScreenId) => void }) {
  return (
    <header className="lm-header">
      <div className="lm-header__left">
        <button type="button" className="lm-header__brand" onClick={() => onNavigate('landing')}>
          <BrandMark size="lg" />
        </button>
      </div>

      <div className="lm-header__phase">
        <span className="lm-header__phase-tag">시드</span>
        <span className="lm-header__phase-text">자료 입력 · 결제</span>
      </div>

      <div className="lm-header__right">
        <button type="button" className="lm-header__iconbtn" aria-label="알림">
          <BellIcon />
        </button>
        <button type="button" className="lm-header__avatar" aria-label="내 계정">
          <div className="lm-header__avatar-dot" style={{ background: 'var(--r-citizen-p)' }} />
        </button>
      </div>
    </header>
  );
}

function BellIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 18 18" fill="none">
      <path d="M4 13h10l-1.2-2V8.5A3.8 3.8 0 0 0 9 4.7 3.8 3.8 0 0 0 5.2 8.5V11L4 13Z" stroke="currentColor" strokeWidth="1.4" strokeLinejoin="round" />
      <path d="M7.5 15a1.5 1.5 0 0 0 3 0" stroke="currentColor" strokeWidth="1.4" strokeLinecap="round" />
    </svg>
  );
}

// --------------------------------------------------------------------
// AppShell — 전체 앱 컨테이너. Header + 본문 슬롯.
// --------------------------------------------------------------------
export interface AppShellProps {
  children: ReactNode;
  plaza?: PlazaMeta | null;
  currentScreen: string;
  onNavigate: (id: ScreenId) => void;
  hideHeader?: boolean;
  phaseNavLocked?: boolean;
}

export function AppShell({
  children,
  plaza,
  currentScreen,
  onNavigate,
  hideHeader = false,
  phaseNavLocked = false,
}: AppShellProps) {
  // landing: 헤더 없음. seed: SeedHeader. 그 외(캐스팅~리포트): 공용 AppHeader.
  let header: ReactNode = null;
  if (!hideHeader) {
    header = currentScreen === 'seed'
      ? <SeedHeader onNavigate={onNavigate} />
      : <AppHeader plaza={plaza} currentScreen={currentScreen} onNavigate={onNavigate} phaseNavLocked={phaseNavLocked} />;
  }
  return (
    <div className="lm-shell">
      {header}
      <main className="lm-shell__main">{children}</main>
    </div>
  );
}

// --------------------------------------------------------------------
// ScreenHeader — 본문 안에 들어가는 큰 헤딩.
// --------------------------------------------------------------------
export interface ScreenHeaderProps {
  eyebrow?: ReactNode;
  title?: ReactNode;
  subtitle?: ReactNode;
  actions?: ReactNode;
  meta?: ReactNode;
}

export function ScreenHeader({ eyebrow, title, subtitle, actions, meta }: ScreenHeaderProps) {
  return (
    <div className="lm-screen-header">
      <div className="lm-screen-header__main">
        {eyebrow && <div className="lm-screen-header__eyebrow">{eyebrow}</div>}
        {title && <h1 className="lm-screen-header__title">{title}</h1>}
        {subtitle && <p className="lm-screen-header__sub">{subtitle}</p>}
      </div>
      {(actions || meta) && (
        <div className="lm-screen-header__aside">
          {meta && <div className="lm-screen-header__meta">{meta}</div>}
          {actions && <div className="lm-screen-header__actions">{actions}</div>}
        </div>
      )}
    </div>
  );
}
