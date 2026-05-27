// =====================================================================
// 데모 Live — RAF 로 50R 광장 진행 + mock 액션 피드.
// 백엔드 SSE 대신 generateLiveActions 로 미리 생성된 액션 시퀀스를 라운드별로 push.
// =====================================================================

import { useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { lm } from '@/data/mock';
import type { Action, ActionType, Agent, AgentRegistry, PlazaNode, RoleId } from '@/data/types';
import { AvatarSVG, Button, Stat, ArrowGlyph } from '@/components/atoms';

const TOTAL_ROUNDS = 50;
// 데모 전체 진행 시간 — 너무 짧으면 인지가 안 되고, 너무 길면 지루.
const TOTAL_MS = 28_000;

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
  const final = lm.generatePlaza({ seed: 42, n: 312 });
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
function lerp(a: number, b: number, t: number) { return a + (b - a) * t; }
function easeInOut(t: number) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }

function LivePlaza({ nodes, settle }: { nodes: LiveNode[]; settle: number }) {
  const W = 1680, H = 920;
  const sNodes = useMemo(() => {
    const e = easeInOut(settle);
    return nodes.map((n) => {
      const x = lerp(n.startX, n.finalX, e);
      const y = lerp(n.startY, n.finalY, e);
      const infRise = Math.max(0, (settle - 0.3) / 0.7);
      const inf = lerp(n.startInfluence, n.finalInfluence, easeInOut(infRise));
      return { ...n, _x: x, _y: y, _inf: inf };
    });
  }, [nodes, settle]);
  const sorted = useMemo(() => [...sNodes].sort((a, b) => a._inf - b._inf), [sNodes]);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="lm-live__svg" preserveAspectRatio="xMidYMid meet">
      {[0.25, 0.5, 0.75].map((p, i) => (
        <line key={i} x1={W * p} x2={W * p} y1={40} y2={H - 60} stroke="#C9C1AD" strokeWidth="1" strokeDasharray="3 8"
          opacity={Math.max(0, settle - 0.2) * (p === 0.5 ? 0.6 : 0.4)} />
      ))}
      {sorted.map((n) => {
        const cx = n._x * W;
        const cy = n._y * (H - 100) + 40;
        const r = lm.nodeRadius(n._inf, 1.6, 32);
        return (
          <g key={n.id}>
            {n._inf > 0.3 && <circle className="lm-live__node-shadow" cx={cx} cy={cy + 1.6} r={r * 1.02} fill="#000" opacity="0.08" />}
            <circle className="lm-live__node" cx={cx} cy={cy} r={r} fill={n.color} opacity="0.92" />
          </g>
        );
      })}
    </svg>
  );
}

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
  round: number; utterances: number; likes: number; reposts: number; citations: number;
  follows: number; followers: number; activeAgents: number; feedSize: number;
  tokens: number; latency: string; fallbackPct: string;
}
function computeStats(actions: Action[], round: number, total: number): LiveStats {
  const upto = actions.filter((a) => a.round <= round);
  const utterances = upto.filter((a) => a.type === 'CREATE_POST' || a.type === 'QUOTE_POST').length;
  const likes = upto.filter((a) => a.type === 'LIKE').length;
  const reposts = upto.filter((a) => a.type === 'REPOST').length;
  const citations = upto.filter((a) => a.type === 'QUOTE_POST').length;
  const follows = upto.filter((a) => a.type === 'FOLLOW').length;
  const settle = round / total;
  return {
    round, utterances, likes, reposts, citations, follows,
    followers: 1240 + Math.round(follows * 0.78),
    activeAgents: Math.min(312, 96 + Math.round(settle * 216)),
    feedSize: upto.length,
    tokens: upto.length * 180 + round * 220,
    latency: (1.05 + 0.35 * Math.sin(round / 4) + 0.15 * (1 - settle)).toFixed(2),
    fallbackPct: Math.max(0, 4.2 - settle * 2.4).toFixed(1),
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
        <div className="lm-live__stat"><span className="lm-live__stat-k">활성 에이전트</span><span className="lm-live__stat-v">{stats.activeAgents}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">피드 크기</span><span className="lm-live__stat-v">{stats.feedSize.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">발언</span><span className="lm-live__stat-v">{stats.utterances.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">호응</span><span className="lm-live__stat-v">{stats.likes.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">전파</span><span className="lm-live__stat-v">{stats.reposts.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">인용</span><span className="lm-live__stat-v">{stats.citations.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">팔로우 변화</span><span className="lm-live__stat-v">+{stats.follows.toLocaleString()}</span></div>
        <div className="lm-live__stat"><span className="lm-live__stat-k">팔로워</span><span className="lm-live__stat-v">{stats.followers.toLocaleString()}</span></div>
        <div className="lm-live__stat lm-live__stat--micro"><span className="lm-live__stat-k">토큰</span><span className="lm-live__stat-v lm-live__stat-v--mono">{stats.tokens.toLocaleString()}</span></div>
        <div className="lm-live__stat lm-live__stat--micro"><span className="lm-live__stat-k">LLM 지연</span><span className="lm-live__stat-v lm-live__stat-v--mono">{stats.latency}s</span></div>
        <div className="lm-live__stat lm-live__stat--micro"><span className="lm-live__stat-k">fallback</span><span className="lm-live__stat-v lm-live__stat-v--mono">{stats.fallbackPct}%</span></div>
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

  useEffect(() => {
    startedAtRef.current = performance.now();
    const tick = () => {
      const elapsed = performance.now() - startedAtRef.current;
      const p = Math.max(0, Math.min(1, elapsed / TOTAL_MS));
      setProgress(p);
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
  const stats = useMemo(() => computeStats(allActions, round, total), [allActions, round, total]);

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
            <Stat label="활성 에이전트" value={stats.activeAgents} align="right" />
            {!sidebarOpen && (
              <Button kind="secondary" onClick={() => setSidebarOpen(true)}>활동 피드 열기</Button>
            )}
          </div>
        </header>
        <div className="lm-live__canvas">
          <LivePlaza nodes={nodes} settle={settle} />
          <div className="lm-live__canvas-axis">
            <span style={{ opacity: Math.max(0, settle - 0.2) }}>← 비판적</span>
            <span style={{ opacity: Math.max(0, settle - 0.2) }}>중립</span>
            <span style={{ opacity: Math.max(0, settle - 0.2) }}>우호적 →</span>
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
