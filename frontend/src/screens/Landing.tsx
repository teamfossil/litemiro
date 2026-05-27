// =====================================================================
// 랜딩 (Phase 1) — A Plaza of Many Minds
// (screen-landing.jsx → ES 모듈 + 타입. useMemoLanding → useMemo, window.LM → lm)
// =====================================================================

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { lm } from '@/data/mock';
import { BrandMark } from '@/components/chrome';
import { Button, ArrowGlyph } from '@/components/atoms';
import { useScreenNav } from '@/lib/nav';
import { api, ApiError, type PlazaStatus, type PlazaSummaryItem } from '@/api/client';

// --------------------------------------------------------------------
// HeroPlaza — 우측 영역의 큰 부감 뷰. 정적, 광장 1개 미감.
// --------------------------------------------------------------------
export function HeroPlaza() {
  const nodes = useMemo(() => lm.generatePlaza({ seed: 42, n: 220 }), []);
  const W = 760;
  const H = 1080;
  const sorted = useMemo(() => [...nodes].sort((a, b) => a.influence - b.influence), [nodes]);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="xMidYMid meet" className="lm-landing__hero-svg">
      <line x1={W * 0.5} x2={W * 0.5} y1={40} y2={H - 40} stroke="#C9C1AD" strokeWidth="1" strokeDasharray="3 8" opacity="0.5" />
      {sorted.map((n) => {
        const cx = n.x * W;
        const cy = n.y * H;
        const r = lm.nodeRadius(n.influence, 1.6, 30);
        return (
          <g key={n.id}>
            {n.influence > 0.4 && <circle cx={cx} cy={cy + 1.6} r={r * 1.02} fill="#000" opacity="0.08" />}
            <circle cx={cx} cy={cy} r={r} fill={n.color} opacity="0.92" />
            {n.anchor && <circle cx={cx} cy={cy} r={r + 3} fill="none" stroke={n.color} strokeWidth="1" opacity="0.32" />}
          </g>
        );
      })}
    </svg>
  );
}

// --------------------------------------------------------------------
// RecentPlazaCard — `/api/plazas` 페이지 한 줄.
// 도트 색은 status 매핑 (역할 정보는 summary 에 없음 — Casting 진입 후에야
// agents 가 채워진다).
// --------------------------------------------------------------------
const STATUS_DOT: Record<PlazaStatus, string> = {
  pending: 'var(--ink-4)',
  running: 'var(--accent)',
  composing: 'var(--accent)',
  completed: 'var(--positive)',
  failed: 'var(--negative)',
};

const STATUS_LABEL: Record<PlazaStatus, string> = {
  pending: '대기',
  running: '진행 중',
  composing: '보고서 합성',
  completed: '완료',
  failed: '실패',
};

function RecentPlazaCard({ plaza, onClick }: { plaza: PlazaSummaryItem; onClick: () => void }) {
  const title = plaza.label?.trim() || `광장 ${plaza.plaza_id.slice(0, 8)}`;
  const summary =
    plaza.status === 'failed' && plaza.error
      ? plaza.error
      : `${STATUS_LABEL[plaza.status]} · ${plaza.rounds_done}/${plaza.rounds_total} 라운드 · ${plaza.preset}`;
  return (
    <button type="button" className="lm-landing__recent-card" onClick={onClick}>
      <span className="lm-landing__recent-dot" style={{ background: STATUS_DOT[plaza.status] }} />
      <div className="lm-landing__recent-body">
        <div className="lm-landing__recent-title">{title}</div>
        <div className="lm-landing__recent-summary">{summary}</div>
      </div>
      <div className="lm-landing__recent-time">{formatRelative(plaza.updated_at)}</div>
    </button>
  );
}

// updated_at 은 ISO 8601. 1분 미만은 "방금", 그 뒤로는 분/시/일/주, 7일 넘으면
// YYYY-MM-DD 로 떨어뜨린다. Intl.RelativeTimeFormat 의 단순화 버전 — 외부 라이브
// 안 끌어오려는 의도. SSR/locale 영향 없음.
function formatRelative(iso: string): string {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const diff = Date.now() - t;
  const sec = Math.max(0, Math.round(diff / 1000));
  if (sec < 60) return '방금';
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}분 전`;
  const hour = Math.round(min / 60);
  if (hour < 24) return `${hour}시간 전`;
  const day = Math.round(hour / 24);
  if (day < 7) return `${day}일 전`;
  return iso.slice(0, 10);
}

// --------------------------------------------------------------------
// ScreenLanding — 메인
// --------------------------------------------------------------------
const RECENT_PAGE_SIZE = 5;

export default function Landing() {
  const go = useScreenNav();
  const navigate = useNavigate();
  const recent = useRecentPlazas();

  return (
    <div className="lm-landing">
      <div className="lm-landing__left">
        <div className="lm-landing__top">
          <BrandMark size="lg" />
          <span className="lm-landing__version">v3.4 · beta</span>
        </div>

        <div className="lm-landing__hero">
          <div className="lm-landing__eyebrow">
            <span className="lm-landing__eyebrow-dot" />
            여론 시뮬레이션 · public-opinion simulator
          </div>
          <h1 className="lm-landing__h1">LiteMiro.</h1>
          <p className="lm-landing__tagline">
            이슈 자료 한 건을 올리면,
            <br />
            수백 명의 가상 인격이 광장에서 토론하고,
            <br />
            <strong>그 결과가 당신의 여론 예측입니다.</strong>
          </p>
        </div>

        <div className="lm-landing__how">
          <div className="lm-landing__how-step">
            <span className="lm-landing__how-n">01</span>
            <span className="lm-landing__how-t">이슈 자료 업로드</span>
          </div>
          <div className="lm-landing__how-step">
            <span className="lm-landing__how-n">02</span>
            <span className="lm-landing__how-t">광장 토론 · 최대 50R</span>
          </div>
          <div className="lm-landing__how-step">
            <span className="lm-landing__how-n">03</span>
            <span className="lm-landing__how-t">결과 = 여론 예측</span>
          </div>
        </div>

        <div className="lm-landing__cta">
          <Button kind="primary" size="lg" onClick={() => go('seed')} trailing={<ArrowGlyph dir="right" />}>
            시뮬레이션 시작
          </Button>
          <Button kind="link" onClick={() => navigate('/demo/casting')}>
            예시 시뮬레이션 보기
          </Button>
        </div>

        {recent.show && (
          <div className="lm-landing__recent">
            <div className="lm-landing__recent-head">
              <span className="lm-landing__recent-label">최근 광장</span>
              {recent.total !== null && (
                <span className="lm-landing__recent-count">전체 {recent.total}</span>
              )}
            </div>
            <div className="lm-landing__recent-list">
              {recent.plazas.map((p) => (
                <RecentPlazaCard
                  key={p.plaza_id}
                  plaza={p}
                  onClick={() => go(p.status === 'completed' ? 'report' : 'plaza', p.plaza_id)}
                />
              ))}
            </div>
            {recent.nextCursor !== null && (
              <Button kind="link" onClick={recent.loadMore} disabled={recent.loading}>
                {recent.loading ? '불러오는 중…' : '더 보기'}
              </Button>
            )}
            {recent.error && <div className="lm-landing__recent-error">목록을 불러오지 못했습니다.</div>}
          </div>
        )}
      </div>

      <div className="lm-landing__right">
        <HeroPlaza />
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// useRecentPlazas — `/api/plazas` keyset 페이징 훅.
// 첫 페이지 5건 fetch → next_cursor 있으면 "더 보기" 노출.
// 404/네트워크 실패는 silent (랜딩 메인 흐름은 막지 않음) — 패널은 숨김.
// --------------------------------------------------------------------
interface RecentPlazasState {
  show: boolean;
  plazas: PlazaSummaryItem[];
  total: number | null;
  nextCursor: string | null;
  loading: boolean;
  error: boolean;
  loadMore: () => void;
}

function useRecentPlazas(): RecentPlazasState {
  const [plazas, setPlazas] = useState<PlazaSummaryItem[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    let cancelled = false;
    api
      .listPlazas({ limit: RECENT_PAGE_SIZE })
      .then((res) => {
        if (cancelled) return;
        setPlazas(res.plazas);
        setTotal(res.total);
        setNextCursor(res.next_cursor);
      })
      .catch((e: unknown) => {
        if (cancelled) return;
        // 백엔드 미기동/네트워크 끊김은 조용히 — 랜딩은 정적 자체로 충분히 동작.
        if (!(e instanceof ApiError)) setError(true);
      })
      .finally(() => {
        if (cancelled) return;
        setLoading(false);
        setInitialized(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  const loadMore = () => {
    if (loading || nextCursor === null) return;
    setLoading(true);
    api
      .listPlazas({ limit: RECENT_PAGE_SIZE, cursor: nextCursor })
      .then((res) => {
        setPlazas((prev) => [...prev, ...res.plazas]);
        setTotal(res.total);
        setNextCursor(res.next_cursor);
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  };

  // 초기 로딩 중이거나 비어 있으면 패널 숨김 (목 데이터 없이 깔끔하게).
  const show = initialized && plazas.length > 0;

  return { show, plazas, total, nextCursor, loading, error, loadMore };
}
