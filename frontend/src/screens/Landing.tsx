// =====================================================================
// 랜딩 (Phase 1) — A Plaza of Many Minds
// (screen-landing.jsx → ES 모듈 + 타입. useMemoLanding → useMemo, window.LM → lm)
// =====================================================================

import { useMemo } from 'react';
import { lm } from '@/data/mock';
import type { PlazaNode, RoleId } from '@/data/types';
import { BrandMark } from '@/components/chrome';
import { Button, ArrowGlyph } from '@/components/atoms';
import { useScreenNav } from '@/lib/nav';

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
// RecentPlazaCard
// --------------------------------------------------------------------
function RecentPlazaCard({ title, summary, role, time }: { title: string; summary: string; role: RoleId; time: string }) {
  return (
    <button type="button" className="lm-landing__recent-card">
      <span className="lm-landing__recent-dot" style={{ background: `var(${lm.ROLE_BY_ID[role].cssVar})` }} />
      <div className="lm-landing__recent-body">
        <div className="lm-landing__recent-title">{title}</div>
        <div className="lm-landing__recent-summary">{summary}</div>
      </div>
      <div className="lm-landing__recent-time">{time}</div>
    </button>
  );
}

// --------------------------------------------------------------------
// ScreenLanding — 메인
// --------------------------------------------------------------------
export default function Landing() {
  const go = useScreenNav();

  // (참고용 — 원본 prototype 의 최근 광장 데이터. 현재 레이아웃에서는 미노출.)
  void RecentPlazaCard;

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
          <Button kind="link" onClick={() => go('casting')}>
            예시 시뮬레이션 — 주 4일제 도입
          </Button>
        </div>
      </div>

      <div className="lm-landing__right">
        <HeroPlaza />
      </div>
    </div>
  );
}
