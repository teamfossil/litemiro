// =====================================================================
// 종료 광장 (Terminal Plaza) — S-priority signature screen
// (screen-plaza.jsx → ES 모듈 + 타입. 별칭 훅 → 표준 훅, window.LM → lm)
// =====================================================================

import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { lm } from '@/data/mock';
import type { PlazaNode } from '@/data/types';
import { AvatarSVG, Button, Stat, Pill, ArrowGlyph } from '@/components/atoms';
import { ScreenHeader } from '@/components/chrome';
import { useScreenNav } from '@/lib/nav';
import { api, type PlazaReportResponse } from '@/api/client';
import {
  buildPlazaNodes,
  ideologyLabel,
  STANCE_BUCKETS,
  stanceBucket,
  stanceColor,
  stanceLabel,
  type StanceBucket,
} from '@/lib/plazaNodes';

interface PlazaFiltersState {
  stances: StanceBucket[];
  influenceOnly: boolean;
}

// --------------------------------------------------------------------
// PlazaCanvas — SVG 부감 뷰.
// --------------------------------------------------------------------
function PlazaCanvas({
  nodes,
  selectedId,
  hoverId,
  onHover,
  onSelect,
  filters,
}: {
  nodes: PlazaNode[];
  selectedId: string | null;
  hoverId: string | null;
  onHover: (id: string | null) => void;
  onSelect: (id: string) => void;
  filters: PlazaFiltersState;
}) {
  const VB_W = 1680;
  const VB_H = 920;

  // viewBox 기반 pan + zoom. zoom 1 = 기본, x/y 는 viewBox 원점.
  const [view, setView] = useState({ x: 0, y: 0, zoom: 1 });
  const svgRef = useRef<SVGSVGElement>(null);
  const panRef = useRef({ down: false, startX: 0, startY: 0, startVbX: 0, startVbY: 0, moved: false });

  const vbW = VB_W / view.zoom;
  const vbH = VB_H / view.zoom;

  // wheel 은 React 가 passive 로 붙이므로 preventDefault 가 안 됨 → native 리스너 사용.
  useEffect(() => {
    const svg = svgRef.current;
    if (!svg) return;
    const handler = (e: WheelEvent) => {
      e.preventDefault();
      setView((prev) => {
        const factor = e.deltaY < 0 ? 1.15 : 1 / 1.15;
        const newZoom = Math.max(0.5, Math.min(8, prev.zoom * factor));
        const rect = svg.getBoundingClientRect();
        const px = (e.clientX - rect.left) / rect.width;
        const py = (e.clientY - rect.top) / rect.height;
        const oldVbW = VB_W / prev.zoom;
        const oldVbH = VB_H / prev.zoom;
        const cursorVbX = prev.x + px * oldVbW;
        const cursorVbY = prev.y + py * oldVbH;
        const newVbW = VB_W / newZoom;
        const newVbH = VB_H / newZoom;
        return { x: cursorVbX - px * newVbW, y: cursorVbY - py * newVbH, zoom: newZoom };
      });
    };
    svg.addEventListener('wheel', handler, { passive: false });
    return () => svg.removeEventListener('wheel', handler);
  }, []);

  const handleMouseDown = (e: React.MouseEvent<SVGSVGElement>) => {
    panRef.current = {
      down: true,
      startX: e.clientX,
      startY: e.clientY,
      startVbX: view.x,
      startVbY: view.y,
      moved: false,
    };
  };
  const handleMouseMove = (e: React.MouseEvent<SVGSVGElement>) => {
    if (!panRef.current.down || !svgRef.current) return;
    const dx = e.clientX - panRef.current.startX;
    const dy = e.clientY - panRef.current.startY;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) panRef.current.moved = true;
    const rect = svgRef.current.getBoundingClientRect();
    setView((v) => {
      const curVbW = VB_W / v.zoom;
      const curVbH = VB_H / v.zoom;
      return {
        ...v,
        x: panRef.current.startVbX - (dx * curVbW) / rect.width,
        y: panRef.current.startVbY - (dy * curVbH) / rect.height,
      };
    });
  };
  const handleMouseUp = () => {
    panRef.current.down = false;
  };
  // 드래그-끝 직후의 click 은 무시. 새 mousedown 에서 moved=false 로 재설정됨.
  const handleNodeClick = (id: string) => {
    if (panRef.current.moved) return;
    onSelect(id);
  };

  const handleReset = () => setView({ x: 0, y: 0, zoom: 1 });
  const canReset = view.x !== 0 || view.y !== 0 || view.zoom !== 1;

  const isDimmed = (n: PlazaNode) =>
    filters.stances.length > 0 && !filters.stances.includes(stanceBucket(n.stance ?? 0.5));

  const sorted = useMemo(() => [...nodes].sort((a, b) => a.influence - b.influence), [nodes]);

  return (
    <>
      <svg
        ref={svgRef}
        viewBox={`${view.x} ${view.y} ${vbW} ${vbH}`}
        preserveAspectRatio="xMidYMid meet"
        className="lm-plaza__svg"
        role="img"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={handleMouseUp}
      >
        <defs>
          <filter id="plazaSoftShadow" x="-30%" y="-30%" width="160%" height="160%">
            <feGaussianBlur in="SourceAlpha" stdDeviation="3" />
            <feOffset dx="0" dy="2" result="offset" />
            <feComponentTransfer>
              <feFuncA type="linear" slope="0.16" />
            </feComponentTransfer>
            <feMerge>
              <feMergeNode />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* subtle ideology lane guides */}
        {[0.25, 0.5, 0.75].map((p, i) => (
          <line
            key={`g${i}`}
            x1={VB_W * p}
            x2={VB_W * p}
            y1={40}
            y2={VB_H - 70}
            stroke="#C9C1AD"
            strokeWidth="1"
            strokeDasharray="3 8"
            opacity={p === 0.5 ? 0.55 : 0.35}
          />
        ))}

        {/* nodes */}
        {sorted.map((n) => {
          const cx = n.x * VB_W;
          // 발화 많을수록 위로 (Live 와 동일 방향). n.y 는 발화량 정규화.
          const cy = (1 - n.y) * (VB_H - 130) + 40;
          // 반지름 = 받은호응 sqrt 스케일 (Live: 2 + sqrt(recv/max)*26). n.influence
          // 가 이미 sqrt 정규화값 [0,1] 이라 선형으로 스케일만 곱한다.
          const r = 2 + n.influence * 26;
          const isSelected = selectedId === n.id;
          const isHover = hoverId === n.id;
          const dim = isDimmed(n);
          const fill = n.color;
          const opacity = dim ? 0.18 : isSelected || isHover ? 1 : 0.92;

          return (
            <g
              key={n.id}
              onMouseEnter={() => onHover(n.id)}
              onMouseLeave={() => onHover(null)}
              onClick={() => handleNodeClick(n.id)}
              style={{ cursor: n.anchor || n.kind === 'derived-viral' ? 'pointer' : 'default' }}
            >
              {n.influence > 0.35 && !dim && <circle cx={cx} cy={cy + 1.8} r={r * 1.02} fill="#000" opacity="0.08" />}
              <circle cx={cx} cy={cy} r={r} fill={fill} opacity={opacity} style={{ transition: 'opacity 120ms' }} />
              {n.anchor && !dim && <circle cx={cx} cy={cy} r={r + 4} fill="none" stroke={fill} strokeWidth="1.2" opacity={isSelected ? 0.9 : 0.32} />}
              {isSelected && <circle cx={cx} cy={cy} r={r + 14} fill="none" stroke="#1A1813" strokeWidth="1" strokeDasharray="3 5" opacity="0.5" />}
            </g>
          );
        })}

        {/* 축 라벨 — SVG 내부에 두어 pan/zoom 과 함께 이동·확대된다. x=ideology. */}
        <text x={20} y={VB_H - 20} className="lm-plaza__svg-axis" textAnchor="start">← 진보</text>
        <text x={VB_W / 2} y={VB_H - 20} className="lm-plaza__svg-axis" textAnchor="middle">중립</text>
        <text x={VB_W - 20} y={VB_H - 20} className="lm-plaza__svg-axis" textAnchor="end">보수 →</text>
      </svg>

      {/* 우상단 컨트롤: 줌 인디케이터 + 초기화 */}
      <div className="lm-plaza__viewctl">
        <span className="lm-plaza__viewctl-zoom">{Math.round(view.zoom * 100)}%</span>
        <button
          type="button"
          className="lm-plaza__viewctl-reset"
          onClick={handleReset}
          disabled={!canReset}
          title="기본 보기로"
        >
          초기화
        </button>
      </div>
    </>
  );
}

// --------------------------------------------------------------------
// PersonaList — 좌측 사이드바. 캐스팅된 앵커 + 바이럴 인격 목록.
// 클릭으로 노드 선택, hover 로 캔버스 노드 강조.
// --------------------------------------------------------------------
function PersonaList({
  nodes,
  selectedId,
  hoverId,
  onSelect,
  onHover,
}: {
  nodes: PlazaNode[];
  selectedId: string | null;
  hoverId: string | null;
  onSelect: (id: string) => void;
  onHover: (id: string | null) => void;
}) {
  // 영향력 큰 순으로 상위 인격만 — 직무(citizen) 대신 stance 진영 라벨을 단다.
  const named = useMemo(
    () => [...nodes].filter((n) => n.name).sort((a, b) => b.influence - a.influence).slice(0, 60),
    [nodes],
  );
  return (
    <aside className="lm-plaza__personas" aria-label="영향력 상위 인격 목록">
      <header className="lm-plaza__personas-head">
        <span className="lm-plaza__personas-tag">CAST · 영향력 상위</span>
        <span className="lm-plaza__personas-count">{named.length}</span>
      </header>
      <div className="lm-plaza__personas-list">
        {named.map((n) => {
          const isActive = selectedId === n.id;
          const isHover = hoverId === n.id;
          return (
            <button
              key={n.id}
              type="button"
              onClick={() => onSelect(n.id)}
              onMouseEnter={() => onHover(n.id)}
              onMouseLeave={() => onHover(null)}
              className={`lm-plaza__personas-item${isActive ? ' is-active' : ''}${isHover ? ' is-hover' : ''}`}
            >
              <span className="lm-plaza__personas-dot" style={{ background: n.color }} />
              <span className="lm-plaza__personas-name">{n.name}</span>
              <span className="lm-plaza__personas-role">{stanceLabel(n.stance ?? 0.5)}</span>
            </button>
          );
        })}
      </div>
    </aside>
  );
}

// --------------------------------------------------------------------
// PlazaTooltip
// --------------------------------------------------------------------
function PlazaTooltip({ node, x, y }: { node: PlazaNode; x: number; y: number }) {
  const stance = node.stance ?? 0.5;
  return (
    <div className="lm-plaza__tooltip" style={{ left: x, top: y }}>
      <div className="lm-plaza__tooltip-who">
        <span className="lm-plaza__tooltip-swatch" style={{ background: node.color }} />
        <span>{stanceLabel(stance)}</span>
      </div>
      {node.name && <div className="lm-plaza__tooltip-name">{node.name}</div>}
      <div className="lm-plaza__tooltip-stats">
        <span>
          입장 <b>{stanceLabel(stance)}</b>
        </span>
        <span>
          성향 <b>{ideologyLabel(node.x)}</b>
        </span>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// DrillInPanel
// --------------------------------------------------------------------
function DrillInPanel({ node, onClose }: { node: PlazaNode | null; onClose: () => void }) {
  if (!node) return null;
  const stance = node.stance ?? 0.5;

  return (
    <aside className="lm-drill" role="dialog" aria-label={`${node.name ?? '익명'} 상세`}>
      <header className="lm-drill__head">
        <div className="lm-drill__head-meta">
          <span className="lm-drill__head-role">
            <span className="lm-plaza__tooltip-swatch" style={{ background: node.color }} /> {stanceLabel(stance)}
          </span>
        </div>
        <button type="button" className="lm-drill__close" onClick={onClose} aria-label="닫기">
          <CloseGlyph />
        </button>
      </header>

      <div className="lm-drill__hero">
        {node.avatar ? (
          <AvatarSVG roleId={node.role} pose={node.avatar.pose} prop={node.avatar.prop} expr={node.avatar.expr} size={96} />
        ) : (
          <div className="lm-drill__hero-dot" style={{ background: node.color }} />
        )}
        <div className="lm-drill__hero-text">
          <div className="lm-drill__hero-name">{node.name || `${stanceLabel(stance)} (익명)`}</div>
        </div>
      </div>

      <div className="lm-drill__stats">
        <Stat
          label="영향력"
          value={Math.round(node.influence * 10000).toLocaleString()}
          delta="받은 호응 sqrt 정규화 [0,1]"
        />
      </div>

      <div className="lm-drill__ideology">
        <span className="lm-drill__ideology-label">성향</span>
        <div className="lm-drill__ideology-track">
          <span className="lm-drill__ideology-thumb" style={{ left: `${node.x * 100}%`, background: node.color }} />
        </div>
        <span className="lm-drill__ideology-value">{ideologyLabel(node.x)}</span>
      </div>
    </aside>
  );
}
function CloseGlyph() {
  return (
    <svg width="14" height="14" viewBox="0 0 14 14">
      <line x1="2" y1="2" x2="12" y2="12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
      <line x1="12" y1="2" x2="2" y2="12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}

// --------------------------------------------------------------------
// PlazaFilters
// --------------------------------------------------------------------
function PlazaFilters({ filters, onChange, totalNodes, nRounds }: { filters: PlazaFiltersState; onChange: (f: PlazaFiltersState) => void; totalNodes: number; nRounds: number | null }) {
  const toggleStance = (s: StanceBucket) => {
    const next = filters.stances.includes(s) ? filters.stances.filter((x) => x !== s) : [...filters.stances, s];
    onChange({ ...filters, stances: next });
  };
  return (
    <div className="lm-plaza__filters">
      <div className="lm-plaza__filters-left">
        <span className="lm-plaza__filters-label">진영</span>
        <div className="lm-plaza__filters-pills">
          <Pill active={filters.stances.length === 0} onClick={() => onChange({ ...filters, stances: [] })}>
            전체 {totalNodes}
          </Pill>
          {STANCE_BUCKETS.map((s) => (
            <Pill key={s.id} color={s.color} active={filters.stances.includes(s.id)} onClick={() => toggleStance(s.id)}>
              {s.name}
            </Pill>
          ))}
        </div>
      </div>
      <div className="lm-plaza__filters-right">
        <Pill active={filters.influenceOnly} onClick={() => onChange({ ...filters, influenceOnly: !filters.influenceOnly })}>
          영향력 상위만
        </Pill>
        {nRounds !== null && (
          <Pill onClick={() => undefined}>
            <span style={{ fontFamily: 'var(--mono)', fontSize: 'var(--t-micro)' }}>
              R{nRounds}/{nRounds}
            </span>
          </Pill>
        )}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// ScreenPlaza — 메인.
// --------------------------------------------------------------------
export default function Plaza() {
  const { plazaId } = useParams<{ plazaId: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  // /demo/plaza 진입 시 mock-only — positions fetch 안 함, nav 도 /demo/* 로.
  const isDemo = location.pathname.startsWith('/demo/');
  const baseGo = useScreenNav(plazaId);
  const go = isDemo
    ? (target: 'live' | 'report' | 'plaza' | 'casting' | 'landing' | 'seed') =>
        navigate(target === 'landing' ? '/' : `/demo/${target}`)
    : baseGo;
  // 데모: Live 최종 mock 화면과 같은 300명 광장. Plaza renderer 는 production
  // 좌표계에 맞춰 y 를 뒤집고 반지름도 positions 기준으로 계산하므로, 데모 노드만
  // 미리 보정해 /demo/live 의 마지막 프레임과 같은 사진이 나오게 한다.
  const mockNodes = useMemo(
    () =>
      lm.generatePlaza({ seed: 42, n: 300 }).map((n) => {
        const liveRadius = lm.nodeRadius(n.influence, 1.6, 32);
        return {
          ...n,
          y: 1 - n.y,
          influence: Math.max(0, Math.min(1, (liveRadius - 2) / 26)),
        };
      }),
    [],
  );
  const [allNodes, setAllNodes] = useState<PlazaNode[]>(isDemo ? mockNodes : []);
  const [selectedId, setSelected] = useState<string | null>(null);
  const [hoverId, setHover] = useState<string | null>(null);
  const [mouse, setMouse] = useState({ x: 0, y: 0 });
  const [filters, setFilters] = useState<PlazaFiltersState>({ stances: [], influenceOnly: false });
  const [report, setReport] = useState<PlazaReportResponse | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);

  // positions(최종 라운드) + agents(정적 stance/이름) fetch — Live 와 같은 인코딩.
  // composing/completed 부터 ready=true. ready=false 면 데모: mock 유지, 비데모: []
  // 유지. agent_id 로 머지 (buildPlazaNodes). 데모는 positions 가 없으니 skip.
  useEffect(() => {
    if (!plazaId || isDemo) return;
    const ac = new AbortController();
    Promise.all([api.getPositions(plazaId, ac.signal), api.getAgents(plazaId, ac.signal)])
      .then(([positions, agentsRes]) => {
        if (!positions.ready || positions.agents.length === 0) return;
        setAllNodes(buildPlazaNodes(agentsRes.agents, positions.agents));
      })
      .catch(() => {
        // mock/빈 상태 유지. abort 도 여기로 떨어져 무시된다.
      });
    return () => ac.abort();
  }, [plazaId, isDemo]);

  // /report fetch — eyebrow / subtitle 의 n_rounds, total actions 용. 빈 응답
  // 이거나 미수신이면 mock fallback 안 쓰고 empty subtitle.
  useEffect(() => {
    if (!plazaId) return;
    const ac = new AbortController();
    api
      .getReport(plazaId, ac.signal)
      .then((res) => {
        setReport(res);
      })
      .catch(() => {
        // 실패 시 report=null → eyebrow/subtitle 의 빈 상태로 떨어짐. abort 포함.
      });
    return () => ac.abort();
  }, [plazaId]);

  // ESC로 드릴인 닫기.
  useEffect(() => {
    if (!selectedId) return;
    const fn = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setSelected(null);
    };
    window.addEventListener('keydown', fn);
    return () => window.removeEventListener('keydown', fn);
  }, [selectedId]);

  const hoverNode = useMemo(() => (hoverId ? allNodes.find((n) => n.id === hoverId) ?? null : null), [hoverId, allNodes]);
  const selectedNode = useMemo(() => (selectedId ? allNodes.find((n) => n.id === selectedId) ?? null : null), [selectedId, allNodes]);

  const visibleNodes = useMemo(() => {
    let xs = allNodes;
    if (filters.influenceOnly) xs = xs.filter((n) => n.influence > 0.18 || n.anchor || n.kind === 'derived-viral');
    return xs;
  }, [allNodes, filters.influenceOnly]);

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!canvasRef.current) return;
    const rect = canvasRef.current.getBoundingClientRect();
    setMouse({ x: e.clientX - rect.left + 12, y: e.clientY - rect.top + 14 });
  };

  // 헤더 분포 — stance 진영(비판/중립/우호) 버킷. Live 경계(0.4/0.6) 일치.
  const distrib = useMemo(() => {
    let critical = 0;
    let neutral = 0;
    let supportive = 0;
    for (const n of allNodes) {
      const bucket = stanceBucket(n.stance ?? 0.5);
      if (bucket === 'critical') critical += 1;
      else if (bucket === 'supportive') supportive += 1;
      else neutral += 1;
    }
    return { critical, neutral, supportive };
  }, [allNodes]);

  return (
    <div className={`lm-plaza${selectedNode ? ' is-drilled' : ''}`}>
      <PersonaList nodes={allNodes} selectedId={selectedId} hoverId={hoverId} onSelect={setSelected} onHover={setHover} />
      <div className="lm-plaza__shellpad">
        <ScreenHeader
          eyebrow={
            report
              ? `Phase 5 · 종료 광장 · R${report.n_rounds}/${report.rounds_total}`
              : 'Phase 5 · 종료 광장'
          }
          title="광장이 닫혔어요."
          subtitle={
            report
              ? `${report.n_agents}명의 인격이 ${report.n_rounds} 라운드 동안 ${report.n_events.toLocaleString()}건의 액션을 일으켰어요. 위에서 내려다본 결과예요.`
              : '위에서 내려다본 광장 결과예요.'
          }
          meta={
            <>
              <Stat label="비판" value={distrib.critical} align="right" />
              <Stat label="중립" value={distrib.neutral} align="right" />
              <Stat label="우호" value={distrib.supportive} align="right" />
            </>
          }
          actions={
            <>
              <Button kind="ghost" onClick={() => go('live')}>
                경과 다시 보기
              </Button>
              <Button kind="primary" onClick={() => go('report')} trailing={<ArrowGlyph dir="right" />}>
                결과 리포트
              </Button>
            </>
          }
        />

        <PlazaFilters filters={filters} onChange={setFilters} totalNodes={allNodes.length} nRounds={report?.n_rounds ?? null} />

        {/* 지도 표기 가이드 — 광장 외부, 박스 없이 인라인. Live 와 같은 인코딩. */}
        <div className="lm-plaza__guide">
          <span><b>가로</b> = 성향(진보↔보수)</span>
          <span className="lm-plaza__guide-sep">·</span>
          <span><b>세로</b> = 발화량</span>
          <span className="lm-plaza__guide-sep">·</span>
          <span><b>색</b> = 입장(비판/중립/우호)</span>
          <span className="lm-plaza__guide-sep">·</span>
          <span><b>크기</b> = 영향력(호응)</span>
        </div>

        <div className="lm-plaza__canvas" ref={canvasRef} onMouseMove={handleMouseMove}>
          <PlazaCanvas nodes={visibleNodes} selectedId={selectedId} hoverId={hoverId} onHover={setHover} onSelect={setSelected} filters={filters} />
          {hoverNode && !selectedNode && <PlazaTooltip node={hoverNode} x={mouse.x} y={mouse.y} />}
        </div>
      </div>

      <DrillInPanel node={selectedNode} onClose={() => setSelected(null)} />
    </div>
  );
}
