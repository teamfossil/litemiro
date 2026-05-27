// =====================================================================
// 데모 캐스팅 — production CastingLoading + CastingReveal 흐름을 mock 으로
// 재현. API 호출 없이 lm.ANCHORS 를 row 카드 그리드로 보여준다.
// 데모 모드: 4초 로딩 → reveal 자동 전환.
// =====================================================================

import { useEffect, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { lm } from '@/data/mock';
import type { Anchor } from '@/data/types';
import { Button, ArrowGlyph } from '@/components/atoms';

const LOADING_MS = 4000;
const TARGET_COUNT = 300;

// Anchor 타입에 topics 가 없어 데모용으로 직접 매핑.
const DEMO_TOPICS: Record<string, string[]> = {
  cm: ['노동', '산업 트랜지션', '경제 보도'],
  hj: ['돌봄·교육', '주4일제', '사회학'],
  js: ['근로기준법', '국회 발의', '정책'],
  pk: ['노동경제학', 'OECD 비교', '산업별 설계'],
  no: ['시범사업 확대', '시민 연대', '산업별 정책'],
};

export default function CastingDemoMock() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<'loading' | 'reveal'>('loading');
  const [agentCount, setAgentCount] = useState(0);
  const [elapsedSec, setElapsedSec] = useState(0);

  useEffect(() => {
    const startedAt = performance.now();
    let raf = 0;
    const tick = () => {
      const elapsed = performance.now() - startedAt;
      const t = Math.min(elapsed / LOADING_MS, 1);
      setAgentCount(Math.round(t * TARGET_COUNT));
      setElapsedSec(Math.floor(elapsed / 1000));
      if (t < 1) {
        raf = requestAnimationFrame(tick);
      } else {
        setPhase('reveal');
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  if (phase === 'loading') {
    return (
      <div className="lm-cast">
        <div className="lm-cast__pad">
          <header className="lm-cast__head">
            <div className="lm-cast__head-left">
              <div className="lm-cast__head-eyebrow">Phase 4 · 인격 생성 (데모)</div>
              <h1 className="lm-cast__head-title">{TARGET_COUNT}명 인격을 만들고 있어요</h1>
              <div className="lm-cast__head-status">
                <span className="lm-cast__head-status-tag">demo</span>
                <span className="lm-cast__head-status-text">
                  데모 LLM 호출 시뮬레이션 · {elapsedSec}초 경과
                </span>
              </div>
            </div>
            <div className="lm-cast__head-actions">
              <Button kind="primary" disabled>
                생성 중…
              </Button>
            </div>
          </header>

          <div className="lm-cast__loading">
            <div className="lm-cast__loading-spinner" aria-hidden="true" />
            <p className="lm-cast__loading-hint">
              데모 모드 — 자료에서 핵심 인물·기관을 뽑고 {TARGET_COUNT}명 시민 인격을 빚는 중입니다.
            </p>
            <p className="lm-cast__loading-progress">
              현재 {agentCount} / {TARGET_COUNT} 명 완료
            </p>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="lm-cast">
      <div className="lm-cast__pad">
        <header className="lm-cast__head">
          <div className="lm-cast__head-left">
            <div className="lm-cast__head-eyebrow">Phase 4 · 인격 추출 완료 (데모)</div>
            <h1 className="lm-cast__head-title">{lm.ANCHORS.length}명의 인격이 모였습니다</h1>
            <div className="lm-cast__head-status">
              <span className="lm-cast__head-status-text">
                각 카드에 이름·역할·성향·관심 주제가 표시돼요. 준비됐다면 광장으로 입장하세요.
              </span>
            </div>
          </div>
          <div className="lm-cast__head-actions">
            <Button kind="primary" onClick={() => navigate('/demo/live')} trailing={<ArrowGlyph dir="right" />}>
              광장으로 입장
            </Button>
          </div>
        </header>

        <div className="lm-cast__rows">
          {lm.ANCHORS.map((a) => (
            <AnchorRow key={a.id} anchor={a} />
          ))}
        </div>
      </div>
    </div>
  );
}

function AnchorRow({ anchor }: { anchor: Anchor }) {
  const role = lm.ROLE_BY_ID[anchor.role];
  const topics = DEMO_TOPICS[anchor.id] ?? [];
  const displayName = anchor.isOrg ? anchor.name : `${anchor.name} ${anchor.title}`;
  return (
    <div className="lm-cast__row" style={{ borderLeftColor: role.color }}>
      <div className="lm-cast__row-name">
        <span className="lm-cast__row-name-role" style={{ color: role.color }}>
          <span className="lm-cast__row-name-role-dot" style={{ background: role.color }} />
          {role.name}
        </span>
        <span className="lm-cast__row-name-text">{displayName}</span>
      </div>
      <div className="lm-cast__row-bar" aria-label={`ideology ${anchor.ideology.toFixed(2)}`}>
        <span className="lm-cast__row-bar-label lm-cast__row-bar-label--left">비판적</span>
        <div className="lm-cast__row-bar-track">
          <span
            className="lm-cast__row-bar-tick"
            style={{ left: `${anchor.ideology * 100}%`, background: role.color }}
          />
        </div>
        <span className="lm-cast__row-bar-label lm-cast__row-bar-label--right">우호적</span>
      </div>
      <div className="lm-cast__row-topics">
        {topics.map((t) => (
          <span key={t} className="lm-cast__row-topic">
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}
