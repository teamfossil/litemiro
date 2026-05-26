// =====================================================================
// 캐스팅 (Phase 4) — Persona Extraction
// (screen-casting.jsx → ES 모듈 + 타입. 별칭 훅 → 표준 훅, window.LM → lm)
// =====================================================================

import { useEffect, useMemo, useRef, useState } from 'react';
import { lm } from '@/data/mock';
import type { Anchor, Seed } from '@/data/types';
import { AvatarSVG, Badge, RoleSwatch, Button, ArrowGlyph } from '@/components/atoms';
import { useScreenNav } from '@/lib/nav';

// --------------------------------------------------------------------
// 타이밍 — 총 8초.
// --------------------------------------------------------------------
const TOTAL_MS = 8000;
const ANCHOR_TIMES = [0.1, 0.25, 0.4, 0.55, 0.7];
const DERIVED_START = 0.75;

function clamp(v: number, a: number, b: number): number {
  return Math.max(a, Math.min(b, v));
}

type SlotState = 'pending' | 'extracting' | 'done';

// --------------------------------------------------------------------
// SeedDocLive — 시드 본문. 추출 진행에 따라 인물 이름이 강조됨.
// --------------------------------------------------------------------
function SeedDocLive({ seed, extractedIds, scanLineY }: { seed: Seed; extractedIds: string[]; scanLineY: number }) {
  return (
    <article className="lm-cast__doc">
      <header className="lm-cast__doc-head">
        <span className="lm-cast__doc-tag">SOURCE · 분석 중인 자료</span>
        <span className="lm-cast__doc-meta">
          {seed.title.length}자 · {seed.paragraphs.length}단락 · 키워드 {seed.keywords.length}
        </span>
      </header>

      <h2 className="lm-cast__doc-title">{seed.title}</h2>

      <div className="lm-cast__doc-body">
        <div className="lm-cast__scanline" style={{ top: `${scanLineY * 100}%` }} />
        {seed.paragraphs.map((para, i) => (
          <p key={i}>
            {para.map((tok, j) => {
              if (!tok.tag) return <span key={j}>{tok.t}</span>;
              const isAnchor = tok.tag === 'anchor';
              const isExtracted = isAnchor && !!tok.anchorId && extractedIds.includes(tok.anchorId);
              const cn = `lm-cast__doc-hl lm-cast__doc-hl--${tok.tag}${isExtracted ? ' is-extracted' : ''}`;
              return (
                <span key={j} className={cn}>
                  {tok.t}
                </span>
              );
            })}
          </p>
        ))}
      </div>

      <footer className="lm-cast__doc-footer">
        {seed.keywords.map((k) => (
          <span key={k} className="lm-cast__doc-tag-chip">
            #{k}
          </span>
        ))}
      </footer>
    </article>
  );
}

// --------------------------------------------------------------------
// AnchorSlot — 우측 1개의 자리.
// --------------------------------------------------------------------
function AnchorSlot({ anchor, state, index }: { anchor: Anchor; state: SlotState; index: number }) {
  const role = lm.ROLE_BY_ID[anchor.role];
  return (
    <div className={`lm-cast__slot lm-cast__slot--${state}`}>
      <span className="lm-cast__slot-index">{String(index + 1).padStart(2, '0')}</span>

      <div className="lm-cast__slot-avatar">
        {state === 'pending' && <span className="lm-cast__slot-pulse" />}
        {state === 'extracting' && <span className="lm-cast__slot-pulse lm-cast__slot-pulse--active" style={{ background: role.color }} />}
        {state === 'done' && <AvatarSVG roleId={anchor.role} pose={anchor.avatar.pose} prop={anchor.avatar.prop} expr={anchor.avatar.expr} size={48} />}
      </div>

      <div className="lm-cast__slot-who">
        {state === 'pending' ? (
          <>
            <div className="lm-cast__slot-skel" style={{ width: 'calc(120px * var(--scale))' }} />
            <div className="lm-cast__slot-skel lm-cast__slot-skel--sm" style={{ width: 'calc(80px * var(--scale))' }} />
          </>
        ) : state === 'extracting' ? (
          <>
            <div className="lm-cast__slot-name lm-cast__slot-name--loading">분석 중…</div>
            <div className="lm-cast__slot-role">
              <RoleSwatch roleId={anchor.role} size={6} /> {role.name}
            </div>
          </>
        ) : (
          <>
            <div className="lm-cast__slot-name">
              {anchor.name}
              {!anchor.isOrg && ' ' + anchor.title}
            </div>
            <div className="lm-cast__slot-role">
              <RoleSwatch roleId={anchor.role} size={6} /> {role.name}
            </div>
          </>
        )}
      </div>

      <div className="lm-cast__slot-pos">
        {state === 'done' && (
          <>
            <span className="lm-cast__slot-pos-label">비판적</span>
            <div className="lm-cast__slot-bar">
              <div className="lm-cast__slot-bar-dot" style={{ left: `${anchor.ideology * 100}%`, background: role.color }} />
            </div>
            <span className="lm-cast__slot-pos-label">우호적</span>
          </>
        )}
      </div>

      <div className="lm-cast__slot-badge">
        {state === 'done' && (
          <Badge tone={anchor.baseInfluence > 0.7 ? 'anchor' : 'default'}>{anchor.baseInfluence > 0.7 ? '주역' : 'extracted'}</Badge>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// DerivedSwarm — derived 군중 생성 시각화.
// --------------------------------------------------------------------
function DerivedSwarm({ progress }: { progress: number }) {
  const totalDots = 48;
  const filled = Math.round(progress * totalDots);
  const realCount = Math.round(progress * 307);
  const colors = ['#B85138', '#C77B4F', '#C9923D', '#A68240', '#8F6B3D', '#6D8FA6', '#4F7591', '#7896A0', '#4F7B6E', '#6E8770', '#8B8170', '#6E6D7D'];
  return (
    <div className="lm-cast__derived-card">
      <div className="lm-cast__derived-head">
        <span className="lm-cast__derived-tag">+ 군중 인격 (derived)</span>
        <span className="lm-cast__derived-count">{progress < 1 ? `${realCount} / 307 명 생성 중…` : '307명 군중 인격 준비 완료'}</span>
      </div>
      <div className="lm-cast__derived-grid">
        {[...Array(totalDots)].map((_, i) => {
          const on = i < filled;
          const c = colors[i % colors.length];
          return (
            <span
              key={i}
              className={`lm-cast__derived-dot${on ? ' is-on' : ''}`}
              style={{ background: on ? c : undefined, transitionDelay: `${i * 18}ms` }}
            />
          );
        })}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// ScreenCasting — 메인.
// --------------------------------------------------------------------
export default function Casting() {
  const go = useScreenNav();
  const seed = lm.SEED;
  const anchors = lm.ANCHORS;

  const [t, setT] = useState(0);
  const startedAtRef = useRef<number>(0);
  const rafRef = useRef<number>(0);
  const navTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  useEffect(() => {
    startedAtRef.current = performance.now();
    const tick = () => {
      const elapsed = performance.now() - startedAtRef.current;
      const newT = clamp(elapsed / TOTAL_MS, 0, 1);
      setT(newT);
      if (newT < 1) {
        rafRef.current = requestAnimationFrame(tick);
      }
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => {
      cancelAnimationFrame(rafRef.current);
      if (navTimerRef.current) clearTimeout(navTimerRef.current);
    };
  }, []);

  const extractedIds = useMemo(() => {
    return ANCHOR_TIMES.map((at, i) => (t >= at ? anchors[i].id : null)).filter(Boolean) as string[];
  }, [t, anchors]);

  const extractingIdx = useMemo(() => {
    for (let i = 0; i < ANCHOR_TIMES.length; i++) {
      const at = ANCHOR_TIMES[i];
      if (t >= at - 0.04 && t < at) return i;
    }
    return -1;
  }, [t]);

  const derivedProgress = clamp((t - DERIVED_START) / (1 - DERIVED_START), 0, 1);
  const done = t >= 1.0;

  const scanLineY = t < 0.75 ? ((t / 0.75) * 1.0) % 1.0 : 1.0;

  const phase = useMemo(() => {
    if (done) return { tag: '준비 완료', text: '광장이 곧 열립니다.' };
    if (t < 0.05) return { tag: '01 문서 분석', text: '자료를 읽고 있어요.' };
    if (t < DERIVED_START) {
      const justExtracted = anchors.find((a) => extractedIds[extractedIds.length - 1] === a.id);
      return { tag: '02 인격 추출', text: justExtracted ? `${justExtracted.name} · 추출 완료` : '인물을 찾고 있어요.' };
    }
    return { tag: '03 군중 생성', text: '익명 시민·전문가 인격을 만들고 있어요.' };
  }, [t, done, anchors, extractedIds]);

  const handleSkip = () => {
    cancelAnimationFrame(rafRef.current);
    if (navTimerRef.current) clearTimeout(navTimerRef.current);
    go('live');
  };

  return (
    <div className="lm-cast">
      <div className="lm-cast__pad">
        {/* COMPACT HEADER */}
        <header className="lm-cast__head">
          <div className="lm-cast__head-left">
            <div className="lm-cast__head-eyebrow">Phase 4 · 페르소나 생성</div>
            <h1 className="lm-cast__head-title">{done ? '인격이 모두 모였어요.' : '문서를 읽고 인격을 만들고 있어요.'}</h1>
            <div className="lm-cast__head-status">
              <span className="lm-cast__head-status-tag">{phase.tag}</span>
              <span className="lm-cast__head-status-text">{phase.text}</span>
            </div>
          </div>
          <div className="lm-cast__head-actions">
            {!done && (
              <Button kind="ghost" onClick={handleSkip}>
                건너뛰기
              </Button>
            )}
            <Button kind="primary" disabled={!done} onClick={handleSkip} trailing={<ArrowGlyph dir="right" />}>
              {done ? '광장으로 입장' : `광장 준비 중 · ${Math.round(t * 100)}%`}
            </Button>
          </div>
        </header>

        {/* PROGRESS BAR */}
        <div className="lm-cast__progress">
          <div className="lm-cast__progress-bar" style={{ width: `${t * 100}%` }} />
          {ANCHOR_TIMES.map((at, i) => (
            <div key={i} className={`lm-cast__progress-mark${t >= at ? ' is-passed' : ''}`} style={{ left: `${at * 100}%` }} />
          ))}
          <div
            className={`lm-cast__progress-mark lm-cast__progress-mark--major${t >= DERIVED_START ? ' is-passed' : ''}`}
            style={{ left: `${DERIVED_START * 100}%` }}
          />
        </div>

        {/* MAIN GRID */}
        <div className="lm-cast__grid">
          <SeedDocLive seed={seed} extractedIds={extractedIds} scanLineY={scanLineY} />

          <div className="lm-cast__slots">
            <header className="lm-cast__slots-head">
              <span className="lm-cast__slots-tag">CAST · 추출된 인격</span>
              <span className="lm-cast__slots-count">
                {extractedIds.length} / {anchors.length} 명
              </span>
            </header>

            <div className="lm-cast__slots-list">
              {anchors.map((a, i) => {
                const state: SlotState = extractedIds.includes(a.id) ? 'done' : extractingIdx === i ? 'extracting' : 'pending';
                return <AnchorSlot key={a.id} anchor={a} state={state} index={i} />;
              })}
            </div>

            <DerivedSwarm progress={derivedProgress} />
          </div>
        </div>
      </div>
    </div>
  );
}
