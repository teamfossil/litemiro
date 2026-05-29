// =====================================================================
// 종료 광장 (Terminal Plaza) — S-priority signature screen
// (screen-plaza.jsx → ES 모듈 + 타입. 별칭 훅 → 표준 훅, window.LM → lm)
// =====================================================================

import { useEffect, useMemo, useRef, useState } from 'react';
import { useLocation, useNavigate, useParams } from 'react-router-dom';
import { lm } from '@/data/mock';
import type { GroupId, PlazaNode } from '@/data/types';
import { AvatarSVG, RoleSwatch, Button, Stat, Pill, ArrowGlyph } from '@/components/atoms';
import { ScreenHeader } from '@/components/chrome';
import { useScreenNav } from '@/lib/nav';
import { api, type PlazaReportResponse } from '@/api/client';
import { mapBackendRoleToRoleId } from '@/lib/roles';

interface PlazaFiltersState {
  groups: GroupId[];
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

  const isDimmed = (n: PlazaNode) => filters.groups.length > 0 && !filters.groups.includes(lm.ROLE_BY_ID[n.role].group);

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
          const cy = n.y * (VB_H - 130) + 40;
          const r = lm.nodeRadius(n.influence, 2, 36);
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

        {/* 축 라벨 — SVG 내부에 두어 pan/zoom 과 함께 이동·확대된다. */}
        <text x={20} y={VB_H - 20} className="lm-plaza__svg-axis" textAnchor="start">← 비판적</text>
        <text x={VB_W / 2} y={VB_H - 20} className="lm-plaza__svg-axis" textAnchor="middle">중립</text>
        <text x={VB_W - 20} y={VB_H - 20} className="lm-plaza__svg-axis" textAnchor="end">우호적 →</text>
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
  const named = nodes.filter((n) => n.name && (n.anchor || n.kind === 'derived-viral'));
  return (
    <aside className="lm-plaza__personas" aria-label="캐스팅 + 바이럴 인격 목록">
      <header className="lm-plaza__personas-head">
        <span className="lm-plaza__personas-tag">CAST · 캐스팅 + 바이럴</span>
        <span className="lm-plaza__personas-count">{named.length}</span>
      </header>
      <div className="lm-plaza__personas-list">
        {named.map((n) => {
          const role = lm.ROLE_BY_ID[n.role];
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
              <span className="lm-plaza__personas-name">
                {n.kind === 'derived-viral' && <em className="lm-plaza__personas-viral">viral</em>}
                {n.kind === 'derived-viral' ? n.firstName || n.name : n.name}
              </span>
              <span className="lm-plaza__personas-role">{role.name}</span>
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
  const role = lm.ROLE_BY_ID[node.role];
  return (
    <div className="lm-plaza__tooltip" style={{ left: x, top: y }}>
      <div className="lm-plaza__tooltip-who">
        <RoleSwatch roleId={node.role} size={10} />
        <span>{role.name}</span>
      </div>
      {node.name && <div className="lm-plaza__tooltip-name">{node.name}</div>}
      <div className="lm-plaza__tooltip-stats">
        <span>
          입장 <b>{ideologyLabel(node.x)}</b>
        </span>
        <span>
          영향력 <b>{Math.round(node.influence * 1000).toLocaleString()}</b>
        </span>
      </div>
    </div>
  );
}
function ideologyLabel(x: number): string {
  if (x < 0.32) return '비판적';
  if (x < 0.42) return '비판적-중립';
  if (x < 0.58) return '중립';
  if (x < 0.68) return '중립-우호적';
  return '우호적';
}

// --------------------------------------------------------------------
// DrillInPanel
// --------------------------------------------------------------------
function DrillInPanel({ node, onClose }: { node: PlazaNode | null; onClose: () => void }) {
  if (!node) return null;
  const role = lm.ROLE_BY_ID[node.role];

  return (
    <aside className="lm-drill" role="dialog" aria-label={`${node.name} 상세`}>
      <header className="lm-drill__head">
        <div className="lm-drill__head-meta">
          <span className="lm-drill__head-role">
            <RoleSwatch roleId={node.role} size={10} /> {role.name}
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
          <div className="lm-drill__hero-name">{node.name || `${role.name} (익명)`}</div>
        </div>
      </div>

      <div className="lm-drill__stats">
        <Stat
          label="영향력"
          value={Math.round(node.influence * 10000).toLocaleString()}
          delta="follow 가중 정규화 [0,1]"
        />
      </div>

      <div className="lm-drill__ideology">
        <span className="lm-drill__ideology-label">위치</span>
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
  const toggleGroup = (g: GroupId) => {
    const next = filters.groups.includes(g) ? filters.groups.filter((x) => x !== g) : [...filters.groups, g];
    onChange({ ...filters, groups: next });
  };
  return (
    <div className="lm-plaza__filters">
      <div className="lm-plaza__filters-left">
        <span className="lm-plaza__filters-label">진영</span>
        <div className="lm-plaza__filters-pills">
          <Pill active={filters.groups.length === 0} onClick={() => onChange({ ...filters, groups: [] })}>
            전체 {totalNodes}
          </Pill>
          {lm.GROUPS.map((g) => (
            <Pill key={g.id} color={g.color} active={filters.groups.includes(g.id)} onClick={() => toggleGroup(g.id)}>
              {g.name}
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
  // /demo/plaza 진입 시 mock-only — /layout fetch 안 함, nav 도 /demo/* 로.
  const isDemo = location.pathname.startsWith('/demo/');
  const baseGo = useScreenNav(plazaId);
  const go = isDemo
    ? (target: 'live' | 'report' | 'plaza' | 'casting' | 'landing' | 'seed') =>
        navigate(target === 'landing' ? '/' : `/demo/${target}`)
    : baseGo;
  // mock 312 노드를 초기값으로 — /layout 응답으로 교체. ready=false 면 mock 유지.
  const mockNodes = useMemo(() => lm.generatePlaza({ seed: 42, n: 312 }), []);
  const [allNodes, setAllNodes] = useState<PlazaNode[]>(mockNodes);
  const [selectedId, setSelected] = useState<string | null>(null);
  const [hoverId, setHover] = useState<string | null>(null);
  const [mouse, setMouse] = useState({ x: 0, y: 0 });
  const [filters, setFilters] = useState<PlazaFiltersState>({ groups: [], influenceOnly: false });
  const [report, setReport] = useState<PlazaReportResponse | null>(null);
  const canvasRef = useRef<HTMLDivElement>(null);

  // /layout fetch — composing/completed 부터 의미 있는 응답. pending/running 은
  // ready=false → mock 유지.
  useEffect(() => {
    if (!plazaId) return;
    const ac = new AbortController();
    api
      .getLayout(plazaId, ac.signal)
      .then((res) => {
        if (!res.ready || res.agents.length === 0) return;
        const nodes: PlazaNode[] = res.agents.map((a) => {
          const roleId = mapBackendRoleToRoleId(a.role);
          const role = lm.ROLE_BY_ID[roleId];
          return {
            id: a.id,
            name: a.name,
            role: roleId,
            kind: 'anchor',
            x: a.x,
            y: a.y,
            influence: a.influence,
            color: role.color,
            anchor: true,
          };
        });
        setAllNodes(nodes);
      })
      .catch(() => {
        // mock 유지. abort 도 여기로 떨어져 무시된다.
      });
    return () => ac.abort();
  }, [plazaId]);

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

  const distrib = useMemo(() => {
    const pro = allNodes.filter((n) => n.x < 0.42).length;
    const mid = allNodes.filter((n) => n.x >= 0.42 && n.x < 0.58).length;
    const con = allNodes.filter((n) => n.x >= 0.58).length;
    return { pro, mid, con };
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
              <Stat label="비판적" value={distrib.pro} align="right" />
              <Stat label="중립" value={distrib.mid} align="right" />
              <Stat label="우호적" value={distrib.con} align="right" />
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

        {/* 지도 표기 가이드 — 광장 외부, 박스 없이 인라인 */}
        <div className="lm-plaza__guide">
          <span><b>위치</b> = 입장</span>
          <span className="lm-plaza__guide-sep">·</span>
          <span><b>색</b> = 역할</span>
          <span className="lm-plaza__guide-sep">·</span>
          <span><b>크기</b> = 영향력</span>
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
