// =====================================================================
// 데모 Live — RAF 로 50R 광장 진행 + mock 액션 피드.
// 백엔드 SSE 대신 generateLiveActions 로 미리 생성된 액션 시퀀스를 라운드별로 push.
// =====================================================================

import { memo, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { lm } from '@/data/mock';
import type { Action, ActionType, Agent, AgentRegistry, PlazaNode, RoleId } from '@/data/types';
import { AvatarSVG, Button, Stat, ArrowGlyph } from '@/components/atoms';

const TOTAL_ROUNDS = 50;
// 데모 전체 진행 시간. 라운드당 간격(= TOTAL_MS/TOTAL_ROUNDS)이 노드 transition
// 보다 넉넉히 길어야 "라운드마다 한 번씩 또렷이 미끄러지는" production 느낌이
// 난다. 42s/50 ≈ 840ms/라운드 > transition 0.7s → glide 후 살짝 쉬고 다음 라운드.
const TOTAL_MS = 42_000;

function buildAgentRegistry(): AgentRegistry {
  const ANCHORS = lm.ANCHORS;
  const ROLE_BY_ID = lm.ROLE_BY_ID;
  const rng = lm.mulberry32(99);

  const agents: Agent[] = ANCHORS.map((a) => ({
    id: a.id,
    name: a.name + (a.isOrg ? '' : ' ' + a.title),
    short: a.name,
    role: a.role,
    kind: 'anchor',
    avatar: a.avatar,
  }));

  const weights = lm.ROLE_COUNT_WEIGHT;
  const totalW = Object.values(weights).reduce((s, x) => s + x, 0);
  for (let i = 0; i < 30; i++) {
    let pick = rng() * totalW;
    let roleId: RoleId = 'citizen_m';
    for (const [id, w] of Object.entries(weights)) {
      pick -= w;
      if (pick <= 0) {
        roleId = id as RoleId;
        break;
      }
    }
    agents.push({
      id: `d${i}`,
      name: `${ROLE_BY_ID[roleId].name} · 익명 #${i + 1}`,
      short: `익명 #${i + 1}`,
      role: roleId,
      kind: 'derived',
    });
  }

  agents.push({
    id: 'viral-47',
    name: '시민·익명 #47',
    short: '시민·익명 #47',
    role: 'citizen_p',
    kind: 'derived-viral',
  });

  return { list: agents, byId: Object.fromEntries(agents.map((a) => [a.id, a])) };
}

const DERIVED_POSTS = [
  '근로시간 단축은 임금 보전이 없으면 의미가 없어요.',
  '시범사업 3년치 자료부터 공개해주세요. 결과로 말합시다.',
  '평균만 보지 말고, 시급제 노동자 사정도 봐야 해요.',
  '돌봄 노동의 시간 1시간이 한 가족의 저녁을 바꿉니다.',
  '한국 노동시장 특수성이 OECD 비교에 안 들어가 있어요.',
  '선언적 도입은 위험합니다. 산업별 트랜지션 설계가 우선.',
  '오히려 격차가 더 벌어질 위험은 없을까요.',
  '발의안 핵심은 강제가 아니라 권리 명시라는 점.',
  '시민 표본이 좁은 거 같습니다. 자영업자 의견도 필요.',
  '일과 삶의 균형이 결국 본질입니다.',
  '저는 매일 11시간 일해요. 4일제는 다른 세상 얘기 같아요.',
  '기업 부담 분담 구조부터 합의돼야 합니다.',
  '시범 결과는 좋았어요. 만족도 78%, 매출 영향 없음.',
  '시간 줄이고 단가 그대로면 청구액 폭등할 텐데요.',
  '아이들 학원 끝나는 시간이랑 안 맞아서 의미 없어요.',
  '제도 도입 전에 5인 미만 사업장 보호부터 합시다.',
];

function pickFrom<T>(arr: T[], rng: () => number): T {
  return arr[Math.floor(rng() * arr.length)];
}

function generateLiveActions(agents: AgentRegistry, totalRounds = TOTAL_ROUNDS): Action[] {
  const rng = lm.mulberry32(42);
  const out: Action[] = [];

  for (const aId of Object.keys(lm.QUOTES)) {
    const id = aId === 'n-viral-47' ? 'viral-47' : aId;
    if (!agents.byId[id]) continue;
    for (const q of lm.QUOTES[aId]) {
      out.push({
        round: q.round,
        agentId: id,
        type: 'CREATE_POST',
        content: q.text,
        citesAccum: q.citations,
        repostsAccum: q.propagations,
      });
    }
  }

  const anchorIds = lm.ANCHORS.map((a) => a.id);
  const derivedIds = agents.list.filter((a) => a.kind !== 'anchor').map((a) => a.id);

  for (let r = 1; r <= totalRounds; r++) {
    const burst = 3 + Math.floor((r / totalRounds) * 4 + rng() * 3);
    for (let i = 0; i < burst; i++) {
      const agentId = pickFrom(derivedIds, rng);
      const targetId = pickFrom(anchorIds, rng);
      const t = rng();
      if (t < 0.34) out.push({ round: r, agentId, type: 'LIKE', targetId });
      else if (t < 0.56) out.push({ round: r, agentId, type: 'REPOST', targetId });
      else if (t < 0.72) out.push({ round: r, agentId, type: 'FOLLOW', targetId });
      else if (t < 0.88) out.push({ round: r, agentId, type: 'QUOTE_POST', targetId, content: pickFrom(DERIVED_POSTS, rng) });
      else out.push({ round: r, agentId, type: 'CREATE_POST', content: pickFrom(DERIVED_POSTS, rng) });
    }
  }
  out.forEach((a, i) => { a._i = i; });
  out.sort((a, b) => (a.round === b.round ? (a._i ?? 0) - (b._i ?? 0) : a.round - b.round));
  return out;
}

interface LiveNode extends PlazaNode {
  startX: number; startY: number; finalX: number; finalY: number;
  startInfluence: number; finalInfluence: number;
}

function generateLiveNodes(): LiveNode[] {
  const final = lm.generatePlaza({ seed: 42, n: 300 });
  const rng = lm.mulberry32(99);
  return final.map((n) => ({
    ...n,
    startX: 0.1 + rng() * 0.8,
    startY: 0.1 + rng() * 0.8,
    finalX: n.x,
    finalY: n.y,
    startInfluence: 0.02 + rng() * 0.05,
    finalInfluence: n.influence,
  }));
}

function lerp(a: number, b: number, t: number) {
  return a + (b - a) * t;
}

function easeInOut(t: number) {
  return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2;
}

const LivePlaza = memo(function LivePlaza({ nodes, round, total }: { nodes: LiveNode[]; round: number; total: number }) {
  const W = 1680, H = 920;
  // production Live 와 같은 방식: 라운드 단위(discrete)로만 위치/크기를 갱신하고,
  // 노드 <g> 의 CSS transform transition 이 라운드 사이를 부드럽게 보간한다.
  // settle(매 프레임)이 아니라 round 에만 의존 → memo 로 라운드당 1번만 리렌더
  // (300 노드를 매 프레임 다시 그리지 않아 안 끊긴다).
  const t = Math.min(1, round / Math.max(1, total));
  const placed = useMemo(
    () =>
      nodes
        .map((n) => ({
          id: n.id,
          color: n.color,
          cx: lerp(n.startX, n.finalX, t) * W,
          cy: lerp(n.startY, n.finalY, t) * (H - 100) + 40,
          r: lm.nodeRadius(lerp(n.startInfluence, n.finalInfluence, t), 1.6, 32),
          shadow: n.finalInfluence > 0.3 && t > 0.4,
        }))
        .sort((a, b) => a.r - b.r), // 작은 점 먼저(뒤), 큰 점 나중(앞)에 그려 위로
    [nodes, t],
  );

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="lm-live__svg" preserveAspectRatio="xMidYMid meet">
      {[0.25, 0.5, 0.75].map((p, i) => (
        <line key={i} x1={W * p} x2={W * p} y1={40} y2={H - 60} stroke="#C9C1AD" strokeWidth="1" strokeDasharray="3 8"
          opacity={p === 0.5 ? 0.6 : 0.4} />
      ))}
      {placed.map((n) => (
        <g
          key={n.id}
          style={{ transform: `translate(${n.cx}px, ${n.cy}px)`, transition: 'transform 0.7s ease', willChange: 'transform' }}
        >
          {n.shadow && <circle className="lm-live__node-shadow" cx={0} cy={1.6} r={n.r * 1.02} fill="#000" opacity="0.08" />}
          <circle className="lm-live__node" cx={0} cy={0} r={n.r} fill={n.color} opacity="0.92" />
        </g>
      ))}
    </svg>
  );
});

const ACTION_LABELS: Record<ActionType, { label: string; tone: string }> = {
  CREATE_POST: { label: '발언', tone: 'create' },
  QUOTE_POST: { label: '인용', tone: 'quote' },
  REPOST: { label: '전파', tone: 'repost' },
  LIKE: { label: '호응', tone: 'like' },
  FOLLOW: { label: '팔로우', tone: 'follow' },
};
function ActionBadge({ type }: { type: ActionType }) {
  const meta = ACTION_LABELS[type] || { label: type, tone: 'create' };
  return <span className={`lm-live__act-badge lm-live__act-badge--${meta.tone}`}>{meta.label}</span>;
}

function AgentChip({ agent, size = 'sm' }: { agent?: Agent; size?: 'sm' | 'lg' }) {
  if (!agent) return null;
  const role = lm.ROLE_BY_ID[agent.role];
  return (
    <span className={`lm-live__agent lm-live__agent--${size}`}>
      {agent.kind === 'anchor' && agent.avatar ? (
        <AvatarSVG roleId={agent.role} pose={agent.avatar.pose} prop={agent.avatar.prop} expr={agent.avatar.expr} size={size === 'lg' ? 32 : 22} />
      ) : (
        <span className="lm-live__agent-dot" style={{
          background: role.color,
          width: size === 'lg' ? 'calc(22px * var(--scale))' : 'calc(14px * var(--scale))',
          height: size === 'lg' ? 'calc(22px * var(--scale))' : 'calc(14px * var(--scale))',
        }} />
      )}
      <span className="lm-live__agent-name">
        {agent.short || agent.name}
        {agent.kind === 'derived-viral' && <em className="lm-live__viral-tag">viral</em>}
      </span>
    </span>
  );
}

function ActionItem({ action, agents }: { action: Action; agents: AgentRegistry }) {
  const agent = agents.byId[action.agentId];
  const target = action.targetId ? agents.byId[action.targetId] : null;
  const hasContent = action.type === 'CREATE_POST' || action.type === 'QUOTE_POST';
  return (
    <article className={`lm-live__act lm-live__act--${ACTION_LABELS[action.type].tone}`}>
      <header className="lm-live__act-head">
        <span className="lm-live__act-round">R{action.round}</span>
        <ActionBadge type={action.type} />
        <AgentChip agent={agent} />
      </header>
      {hasContent && <p className="lm-live__act-body">{action.content}</p>}
      {target && (
        <div className="lm-live__act-target">
          <span className="lm-live__act-target-arrow">↳</span>
          <AgentChip agent={target} />
        </div>
      )}
    </article>
  );
}

interface LiveStats {
  round: number;
  utterances: number;
  likes: number;
  reposts: number;
  citations: number;
  follows: number;
  feedSize: number;
}
function computeStats(actions: Action[], round: number): LiveStats {
  const upto = actions.filter((a) => a.round <= round);
  const utterances = upto.filter((a) => a.type === 'CREATE_POST' || a.type === 'QUOTE_POST').length;
  const likes = upto.filter((a) => a.type === 'LIKE').length;
  const reposts = upto.filter((a) => a.type === 'REPOST').length;
  const citations = upto.filter((a) => a.type === 'QUOTE_POST').length;
  const follows = upto.filter((a) => a.type === 'FOLLOW').length;
  return {
    round,
    utterances,
    likes,
    reposts,
    citations,
    follows,
    feedSize: upto.length,
  };
}

function liveStatus(round: number, total: number) {
  const settle = round / total;
  if (settle < 0.05) return { tag: '오프닝', text: '광장이 열렸어요. 인격들이 들어오고 있어요.' };
  if (settle < 0.3) return { tag: '입장', text: '아직 누가 어디로 갈지 정해지지 않았어요.' };
  if (settle < 0.55) return { tag: '진영 형성', text: '진영이 잡히고 있어요. 사람들이 옆자리를 찾아요.' };
  if (settle < 0.8) return { tag: '화제 부상', text: '몇몇 발언이 화제를 모으고 있어요. 노드가 커져요.' };
  if (settle < 0.98) return { tag: '수렴', text: '인격들이 자리를 잡고 있어요. 광장이 곧 닫혀요.' };
  return { tag: '종료 임박', text: '광장이 닫히고 있어요. 결과를 정리할게요.' };
}

function LiveSidebar({ actions, agents, round, total, stats, onClose }: {
  actions: Action[]; agents: AgentRegistry; round: number; total: number; stats: LiveStats; onClose: () => void;
}) {
  const recent = useMemo(() => actions.filter((a) => a.round <= round).slice(-40).reverse(), [actions, round]);
  return (
    <aside className="lm-live__sidebar">
      <header className="lm-live__sidebar-head">
        <div>
          <div className="lm-live__sidebar-eyebrow">SIDEBAR · 광장의 대화</div>
          <h2 className="lm-live__sidebar-title">활동 피드</h2>
        </div>
        <button type="button" className="lm-live__sidebar-close" onClick={onClose} aria-label="사이드바 닫기">
          <svg width="14" height="14" viewBox="0 0 14 14">
            <line x1="2" y1="2" x2="12" y2="12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
            <line x1="12" y1="2" x2="2" y2="12" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" />
          </svg>
        </button>
      </header>
      <section className="lm-live__stats-grid">
        <div className="lm-live__stat"><span className="lm-live__stat-k">라운드</span><span className="lm-live__stat-v">{stats.round} <small>/ {total}</small></span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">피드 크기</span><span className="lm-live__stat-v">{stats.feedSize.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">발언</span><span className="lm-live__stat-v">{stats.utterances.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">호응</span><span className="lm-live__stat-v">{stats.likes.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">전파</span><span className="lm-live__stat-v">{stats.reposts.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">인용</span><span className="lm-live__stat-v">{stats.citations.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">팔로우 변화</span><span className="lm-live__stat-v">+{stats.follows.toLocaleString()}</span></div>
      </section>
      <section className="lm-live__feed" aria-live="polite">
        <div className="lm-live__feed-head">
          <span className="lm-live__feed-tag">최근 활동 · {recent.length}건</span>
          <span className="lm-live__feed-hint">새 활동이 위로 올라와요</span>
        </div>
        <div className="lm-live__feed-list">
          {recent.length === 0 && <div className="lm-live__feed-empty">광장이 곧 열려요. 첫 발언을 기다리는 중…</div>}
          {recent.map((a, i) => (
            <ActionItem key={`r${a.round}-${i}-${a.agentId}-${a.type}`} action={a} agents={agents} />
          ))}
        </div>
      </section>
    </aside>
  );
}

export default function LiveDemoMock() {
  const navigate = useNavigate();
  const nodes = useMemo(() => generateLiveNodes(), []);
  const agents = useMemo(() => buildAgentRegistry(), []);
  const allActions = useMemo(() => generateLiveActions(agents, TOTAL_ROUNDS), [agents]);

  const [progress, setProgress] = useState(0); // 0..1
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const startedAtRef = useRef<number>(0);
  const rafRef = useRef<number>(0);
  const lastRoundRef = useRef<number>(-1);

  useEffect(() => {
    startedAtRef.current = performance.now();
    const tick = () => {
      const elapsed = performance.now() - startedAtRef.current;
      const p = Math.max(0, Math.min(1, elapsed / TOTAL_MS));
      // 매 프레임 setProgress 하면 부모가 60fps 로 리렌더돼 300 노드 CSS
      // transition 과 메인스레드에서 경쟁 → 끊긴다. 라운드가 바뀔 때(또는 완료)
      // 만 state 를 갱신해 리렌더를 ~50 회로 줄인다. 노드 이동은 CSS 가 보간.
      const r = Math.floor(p * TOTAL_ROUNDS);
      if (r !== lastRoundRef.current || p >= 1) {
        lastRoundRef.current = r;
        setProgress(p);
      }
      if (p < 1) rafRef.current = requestAnimationFrame(tick);
    };
    rafRef.current = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(rafRef.current);
  }, []);

  const round = Math.floor(progress * TOTAL_ROUNDS);
  const total = TOTAL_ROUNDS;
  const settle = progress;
  const isCompleted = progress >= 1;
  const status = isCompleted ? { tag: '광장 종료', text: '결과를 확인하세요.' } : liveStatus(round, total);
  const stats = useMemo(() => computeStats(allActions, round), [allActions, round]);

  return (
    <div className={`lm-live ${sidebarOpen ? 'is-sidebar-open' : ''}`}>
      <div className="lm-live__main">
        <header className="lm-live__head">
          <div className="lm-live__head-left">
            <div className="lm-live__status">
              <span className="lm-live__status-tag">{status.tag}</span>
              <span className="lm-live__status-text">{status.text}</span>
            </div>
          </div>
          <div className="lm-live__head-right">
            <Stat label="라운드" value={`${round} / ${total}`} align="right" />
            <Stat label="발언" value={stats.utterances.toLocaleString()} align="right" />
            {!sidebarOpen && (
              <Button kind="secondary" onClick={() => setSidebarOpen(true)}>활동 피드 열기</Button>
            )}
          </div>
        </header>
        <div className="lm-live__canvas">
          <LivePlaza nodes={nodes} round={round} total={total} />
          <div className="lm-live__legend" style={{ opacity: 1 }}>
            <span className="lm-live__legend-intro">점 1개 = 인격 1명</span>
            <span className="lm-live__legend-head">색 — 주제 입장</span>
            <span className="lm-live__legend-item"><i style={{ background: '#c75c54' }} />비판</span>
            <span className="lm-live__legend-item"><i style={{ background: '#a99f88' }} />중립</span>
            <span className="lm-live__legend-item"><i style={{ background: '#5b87b3' }} />우호</span>
            <span className="lm-live__legend-head">크기 — 영향력(호응)</span>
            <span className="lm-live__legend-size">
              <i style={{ width: 6, height: 6, background: '#8a8275' }} />
              <i style={{ width: 14, height: 14, background: '#8a8275' }} />
              <span>적음 → 많음</span>
            </span>
          </div>
          <div className="lm-live__canvas-axis">
            <span style={{ opacity: 1 }}>← 진보</span>
            <span style={{ opacity: 1 }}>중립</span>
            <span style={{ opacity: 1 }}>보수 →</span>
          </div>
        </div>
        <footer className="lm-live__foot">
          <div className="lm-live__progress">
            <div className="lm-live__progress-bar" style={{ width: `${settle * 100}%` }} />
            {[...Array(total + 1).keys()].filter((i) => i % 10 === 0).map((i) => (
              <div key={i} className="lm-live__progress-tick" style={{ left: `${(i / total) * 100}%` }}>
                <span>R{i}</span>
              </div>
            ))}
          </div>
          <div className="lm-live__foot-actions">
            <Button kind="primary" onClick={() => navigate('/demo/plaza')} trailing={<ArrowGlyph dir="right" />} disabled={!isCompleted}>
              {isCompleted ? '결과 광장 보기' : '광장이 닫히면 결과로'}
            </Button>
          </div>
        </footer>
      </div>
      {sidebarOpen && (
        <LiveSidebar actions={allActions} agents={agents} round={round} total={total} stats={stats} onClose={() => setSidebarOpen(false)} />
      )}
    </div>
  );
}
