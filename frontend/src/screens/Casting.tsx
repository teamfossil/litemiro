// =====================================================================
// 캐스팅 (Phase 4) — Persona Extraction
// 두 모드:
// - /casting/new?ontology=...&preset=...&rounds=...&label=...
//   → CastingReal. /api/ontologies/{id} 를 1.5~2 초 간격으로 폴링, ready 가
//     되면 /api/plazas 를 만들어 /live/{plaza_id} 로 넘긴다. Seed 흐름의
//     실제 진입로.
// - /casting/:plazaId
//   → CastingDemo. 옛 프로토타입의 8 초 fake 애니메이션. Landing 의 데모
//     진입 및 헤더 phase nav 에서 잡아둔다.
// =====================================================================

import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { lm } from '@/data/mock';
import type { Anchor } from '@/data/types';
import { AvatarSVG, Badge, RoleSwatch, Button, ArrowGlyph } from '@/components/atoms';
import { useScreenNav, pathForScreen } from '@/lib/nav';
import { api, ApiError, type OntologyResponse, type Preset } from '@/api/client';
import { avatarFromSeed, mapBackendRoleToRoleId } from '@/lib/roles';

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
// ScanPanel — 자료 스캔 중 표시. 백엔드가 source 본문을 안 줘서 본문 노출
// 없이 진행 표시만. extractedCount 가 늘 때마다 라인이 그려진다.
// --------------------------------------------------------------------
function ScanPanel({ extractedCount, totalCount, scanLineY }: { extractedCount: number; totalCount: number; scanLineY: number }) {
  return (
    <article className="lm-cast__doc">
      <header className="lm-cast__doc-head">
        <span className="lm-cast__doc-tag">SOURCE · 자료 분석</span>
        <span className="lm-cast__doc-meta">
          {extractedCount} / {totalCount} 인격 식별
        </span>
      </header>

      <div className="lm-cast__doc-body lm-cast__doc-body--minimal">
        <div className="lm-cast__scanline" style={{ top: `${scanLineY * 100}%` }} />
        <div className="lm-cast__scan-rows">
          {Array.from({ length: 18 }).map((_, i) => (
            <div key={i} className="lm-cast__scan-row" style={{ width: `${30 + ((i * 17) % 60)}%` }} />
          ))}
        </div>
      </div>
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
// DerivedSwarm — 군중 인격 생성 시각화 (구체 수 미공개).
// 백엔드가 derived 수를 따로 안 알려줘 진행 표시만.
// --------------------------------------------------------------------
function DerivedSwarm({ progress }: { progress: number }) {
  const totalDots = 48;
  const filled = Math.round(progress * totalDots);
  const colors = ['#B85138', '#C77B4F', '#C9923D', '#A68240', '#8F6B3D', '#6D8FA6', '#4F7591', '#7896A0', '#4F7B6E', '#6E8770', '#8B8170', '#6E6D7D'];
  return (
    <div className="lm-cast__derived-card">
      <div className="lm-cast__derived-head">
        <span className="lm-cast__derived-tag">+ 군중 인격</span>
        <span className="lm-cast__derived-count">{progress < 1 ? '군중 인격 생성 중…' : '군중 인격 준비 완료'}</span>
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
// CastingDemo — 옛 8 초 fake 애니메이션. Landing 데모 / phase nav 진입용.
// --------------------------------------------------------------------
function CastingDemo() {
  const { plazaId } = useParams<{ plazaId: string }>();
  const go = useScreenNav(plazaId);
  // 백엔드 /agents 응답으로 채움. 도착 전엔 빈 배열 — 슬롯이 pending 상태로 표시.
  const [anchors, setAnchors] = useState<Anchor[]>([]);
  // 백엔드 sim 이 실제로 'running' 상태가 됐는지 — 8초 mock 타이머와 별개로
  // 트래킹해서 둘 다 만족할 때 광장 입장 활성.
  const [simStarted, setSimStarted] = useState(false);

  const [t, setT] = useState(0);
  const startedAtRef = useRef<number>(0);
  const rafRef = useRef<number>(0);

  // /agents fetch — pending/running 단계에서도 200 (#85). 실패 시 mock fallback 유지.
  useEffect(() => {
    if (!plazaId) return;
    let cancelled = false;
    api
      .getAgents(plazaId)
      .then((res) => {
        if (cancelled) return;
        const mapped: Anchor[] = res.agents.map((a) => ({
          id: a.id,
          name: a.name,
          title: '',
          role: mapBackendRoleToRoleId(a.role),
          avatar: avatarFromSeed(a.avatar_seed),
          ideology: a.ideology,
          baseInfluence: 0.5,
          bio: '',
          isOrg: false,
        }));
        if (mapped.length > 0) setAnchors(mapped);
      })
      .catch(() => {
        // 실패 시 mock 유지 — 화면이 빈 슬롯으로 깨지지 않게.
      });
    return () => {
      cancelled = true;
    };
  }, [plazaId]);

  // SSE 구독 — status='running' 부터는 sim 실제 시작이므로 광장 입장 가능 신호.
  useEffect(() => {
    if (!plazaId) return;
    const stream = api.streamPlazaEvents(plazaId, {
      onStatus: (e) => {
        if (e.status === 'running' || e.status === 'composing' || e.status === 'completed') {
          setSimStarted(true);
        }
      },
    });
    return () => stream.close();
  }, [plazaId]);

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
    };
  }, []);

  // 슬롯에는 ANCHOR_TIMES(5) 만큼만 노출 — 100~500 명의 ontology 라도 화면엔
  // 대표 5명만. 나머지는 DerivedSwarm 으로 표현. anchors 가 5 미만이면 그 수만큼.
  const featuredAnchors = useMemo(
    () => anchors.slice(0, Math.min(anchors.length, ANCHOR_TIMES.length)),
    [anchors],
  );
  const anchorTimes = useMemo(() => ANCHOR_TIMES.slice(0, featuredAnchors.length), [featuredAnchors.length]);

  const extractedIds = useMemo(() => {
    return anchorTimes.map((at, i) => (t >= at ? featuredAnchors[i].id : null)).filter(Boolean) as string[];
  }, [t, featuredAnchors, anchorTimes]);

  const extractingIdx = useMemo(() => {
    for (let i = 0; i < anchorTimes.length; i++) {
      const at = anchorTimes[i];
      if (t >= at - 0.04 && t < at) return i;
    }
    return -1;
  }, [t, anchorTimes]);

  const derivedProgress = clamp((t - DERIVED_START) / (1 - DERIVED_START), 0, 1);
  // 8초 mock 애니메이션 완료 + 백엔드 'running' 둘 다 만족하면 입장 가능.
  // plazaId 없을 때는 (개발/링크 직접 진입) mock 만으로 진행.
  const done = t >= 1.0 && (simStarted || !plazaId);

  const scanLineY = t < 0.75 ? ((t / 0.75) * 1.0) % 1.0 : 1.0;

  const phase = useMemo(() => {
    if (done) return { tag: '준비 완료', text: '광장이 곧 열립니다.' };
    if (t < 0.05) return { tag: '01 문서 분석', text: '자료를 읽고 있어요.' };
    if (t < DERIVED_START) {
      const justExtracted = featuredAnchors.find((a) => extractedIds[extractedIds.length - 1] === a.id);
      return { tag: '02 인격 추출', text: justExtracted ? `${justExtracted.name} · 추출 완료` : '인물을 찾고 있어요.' };
    }
    return { tag: '03 군중 생성', text: '익명 시민·전문가 인격을 만들고 있어요.' };
  }, [t, done, anchors, extractedIds]);

  const handleSkip = () => {
    cancelAnimationFrame(rafRef.current);
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
            <Button kind="primary" disabled={!done} onClick={handleSkip} trailing={<ArrowGlyph dir="right" />}>
              {done ? '광장으로 입장' : `광장 준비 중 · ${Math.round(t * 100)}%`}
            </Button>
          </div>
        </header>

        {/* PROGRESS BAR */}
        <div className="lm-cast__progress">
          <div className="lm-cast__progress-bar" style={{ width: `${t * 100}%` }} />
          {anchorTimes.map((at, i) => (
            <div key={i} className={`lm-cast__progress-mark${t >= at ? ' is-passed' : ''}`} style={{ left: `${at * 100}%` }} />
          ))}
          <div
            className={`lm-cast__progress-mark lm-cast__progress-mark--major${t >= DERIVED_START ? ' is-passed' : ''}`}
            style={{ left: `${DERIVED_START * 100}%` }}
          />
        </div>

        {/* MAIN GRID */}
        <div className="lm-cast__grid">
          <ScanPanel extractedCount={extractedIds.length} totalCount={featuredAnchors.length} scanLineY={scanLineY} />

          <div className="lm-cast__slots">
            <header className="lm-cast__slots-head">
              <span className="lm-cast__slots-tag">CAST · 핵심 인격 {featuredAnchors.length}명</span>
              <span className="lm-cast__slots-count">
                {extractedIds.length} / {featuredAnchors.length} 명
              </span>
            </header>

            <div className="lm-cast__slots-list">
              {featuredAnchors.map((a, i) => {
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

// --------------------------------------------------------------------
// CastingReal — Seed 흐름의 실제 진입. /api/ontologies/{id} 폴링 →
// ready=true 시 /api/plazas POST → /live/{plaza_id} replace. URL 검색어
// (ontology / preset / rounds / label) 만으로 상태가 복원돼 새로고침에도
// 살아남는다.
// --------------------------------------------------------------------
const ONTOLOGY_POLL_INTERVAL_MS = 2_000;
const DEFAULT_ROUNDS = 15;
type RealPhase = 'polling' | 'launching' | 'failed';

function CastingReal() {
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const ontologyId = search.get('ontology') ?? '';
  const labelParam = search.get('label') ?? '';
  const presetParam = search.get('preset') ?? 'standard';
  const roundsParam = Number(search.get('rounds') ?? DEFAULT_ROUNDS);

  const preset: Preset = isPreset(presetParam) ? presetParam : 'standard';
  const rounds =
    Number.isFinite(roundsParam) && roundsParam > 0 ? Math.floor(roundsParam) : DEFAULT_ROUNDS;
  // preset → 목표 인격 수. 기본 표시용. contract.md 의 quick=100 / standard=300 / full=500.
  const targetCount = preset === 'quick' ? 100 : preset === 'full' ? 500 : 300;

  const [status, setStatus] = useState<OntologyResponse | null>(null);
  const [phase, setPhase] = useState<RealPhase>('polling');
  const [error, setError] = useState<string | null>(null);
  const [elapsedSec, setElapsedSec] = useState(0);

  useEffect(() => {
    if (!ontologyId) {
      setError('ontology_id 가 누락됐어요. 시드 화면에서 다시 시작해주세요.');
      setPhase('failed');
      return;
    }

    let cancelled = false;
    let pollTimer: ReturnType<typeof setTimeout> | null = null;
    const startedAt = Date.now();
    const elapsedTimer = window.setInterval(() => {
      if (!cancelled) setElapsedSec(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);

    const tick = async () => {
      if (cancelled) return;
      let onto: OntologyResponse;
      try {
        onto = await api.getOntology(ontologyId);
      } catch (e) {
        if (cancelled) return;
        setError(formatError(e, '상태 조회 실패'));
        setPhase('failed');
        return;
      }
      if (cancelled) return;
      setStatus(onto);

      if (onto.status === 'completed') {
        setPhase('launching');
        try {
          const plaza = await api.createPlaza({
            ontology_id: ontologyId,
            rounds,
            preset,
            label: labelParam || undefined,
          });
          if (!cancelled) {
            // CastingDemo 의 슬롯 애니메이션을 한 번 보여준 뒤 Live 로 가게 한다.
            // CastingDemo 가 8 초 뒤 자동으로 /live/{plazaId} 로 navigate.
            navigate(pathForScreen('casting', plaza.plaza_id), { replace: true });
          }
        } catch (e) {
          if (cancelled) return;
          setError(formatError(e, '광장 열기 실패'));
          setPhase('failed');
        }
        return;
      }
      if (onto.status === 'failed') {
        setError(`인격 생성 실패: ${onto.error ?? '알 수 없는 오류'}`);
        setPhase('failed');
        return;
      }
      pollTimer = setTimeout(tick, ONTOLOGY_POLL_INTERVAL_MS);
    };
    tick();

    return () => {
      cancelled = true;
      if (pollTimer) clearTimeout(pollTimer);
      window.clearInterval(elapsedTimer);
    };
  }, [ontologyId, rounds, preset, labelParam, navigate]);

  const elapsedLabel = formatElapsed(elapsedSec);
  const statusTag = phase === 'failed' ? 'failed' : (status?.status ?? 'pending');
  const headTitle =
    phase === 'failed'
      ? '문제가 발생했어요'
      : phase === 'launching'
        ? '광장을 여는 중…'
        : `${targetCount}명 인격을 만들고 있어요`;
  const headSubtext =
    phase === 'failed'
      ? (error ?? '알 수 없는 오류')
      : phase === 'launching'
        ? '곧 자동으로 광장이 열립니다.'
        : `LLM 호출이 진행되고 있어요 · ${elapsedLabel} 경과`;

  return (
    <div className="lm-cast">
      <div className="lm-cast__pad">
        <header className="lm-cast__head">
          <div className="lm-cast__head-left">
            <div className="lm-cast__head-eyebrow">Phase 1 · 인격 생성</div>
            <h1 className="lm-cast__head-title">{headTitle}</h1>
            <div className="lm-cast__head-status">
              <span className="lm-cast__head-status-tag">{statusTag}</span>
              <span className="lm-cast__head-status-text">{headSubtext}</span>
            </div>
          </div>
          <div className="lm-cast__head-actions">
            {phase === 'failed' ? (
              <Button kind="primary" onClick={() => navigate('/seed', { replace: true })}>
                시드로 돌아가기
              </Button>
            ) : (
              <Button kind="primary" disabled>
                {phase === 'launching' ? '광장 여는 중…' : '생성 중…'}
              </Button>
            )}
          </div>
        </header>

        <div className="lm-cast__real">
          {phase === 'failed' ? (
            <p className="lm-cast__real-error">{error ?? '알 수 없는 오류'}</p>
          ) : (
            <>
              <div className="lm-cast__real-spinner" aria-hidden="true" />
              <p className="lm-cast__real-hint">
                자료에서 핵심 인물·기관을 뽑고 {targetCount}명 시민 인격을 빚는 중입니다.
                보통 분 단위가 걸려요. 이 화면에 머물러 있으면 자동으로 광장이 열립니다.
              </p>
              {status?.agent_count != null && (
                <p className="lm-cast__real-progress">
                  현재 {status.agent_count} / {targetCount} 명 완료
                </p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// Casting (default) — URL 분기 한 줄짜리 디스패처.
// --------------------------------------------------------------------
export default function Casting() {
  const location = useLocation();
  if (location.pathname === '/casting/new') return <CastingReal />;
  return <CastingDemo />;
}

function isPreset(v: string): v is Preset {
  return v === 'quick' || v === 'standard' || v === 'full';
}

function formatElapsed(sec: number): string {
  if (sec < 60) return `${sec}초`;
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  return `${m}분 ${s}초`;
}

function formatError(err: unknown, fallback: string): string {
  if (err instanceof ApiError) {
    const detail = err.message.length > 200 ? err.message.slice(0, 200) + '…' : err.message;
    return `${fallback} (${err.status}): ${detail}`;
  }
  if (err instanceof Error) return `${fallback}: ${err.message}`;
  return fallback;
}
