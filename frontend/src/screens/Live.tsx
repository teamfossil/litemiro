// =====================================================================
// 진행 화면 (Phase 4) — Live
// 좌: force-directed 광장 타임랩스 / 우: 활동 피드 사이드바
// (screen-live.jsx → ES 모듈 + 타입. 별칭 훅 → 표준 훅, window.LM → lm)
// 실 제품: generateLiveActions() → SSE /stream 의 event:"action" 으로 교체.
// =====================================================================

import { memo, useEffect, useMemo, useState } from 'react';
import { useParams } from 'react-router-dom';
import { lm } from '@/data/mock';
import type { Action, ActionType, Agent, AgentRegistry, PlazaNode } from '@/data/types';
import { AvatarSVG, Button, Stat, ArrowGlyph } from '@/components/atoms';
import { useScreenNav } from '@/lib/nav';
import { api, type PlazaActionEvent, type PlazaAgentItem, type PlazaLayoutAgentItem, type PlazaStatus } from '@/api/client';
import { avatarFromSeed, mapBackendRoleToRoleId } from '@/lib/roles';

// --------------------------------------------------------------------
// Plaza 노드 — /layout ready 일 때만 백엔드 좌표로 표시.
// 백엔드가 좌표를 안 줄 땐 frontend 가 채우지 않고 empty state 로 처리.
// --------------------------------------------------------------------
function buildLiveNodes(
  agents: PlazaAgentItem[],
  layoutAgents: PlazaLayoutAgentItem[],
): PlazaNode[] {
  if (layoutAgents.length === 0) return [];
  const agentMap = new Map(agents.map((a) => [a.id, a]));
  return layoutAgents
    .map((la): PlazaNode | null => {
      const a = agentMap.get(la.id);
      if (!a) return null;
      const roleId = mapBackendRoleToRoleId(a.role);
      return {
        id: a.id,
        name: a.name,
        role: roleId,
        kind: 'anchor',
        color: lm.ROLE_BY_ID[roleId].color,
        x: la.x,
        y: la.y,
        influence: la.influence,
      };
    })
    .filter((n): n is PlazaNode => n !== null);
}

// --------------------------------------------------------------------
// LivePlaza — 광장 캔버스 (백엔드 좌표 그대로 그림)
// --------------------------------------------------------------------
function LivePlaza({ nodes }: { nodes: PlazaNode[] }) {
  const W = 1680;
  const H = 920;
  const sorted = useMemo(() => [...nodes].sort((a, b) => a.influence - b.influence), [nodes]);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="lm-live__svg" preserveAspectRatio="xMidYMid meet">
      {[0.25, 0.5, 0.75].map((p, i) => (
        <line
          key={i}
          x1={W * p}
          x2={W * p}
          y1={40}
          y2={H - 60}
          stroke="#C9C1AD"
          strokeWidth="1"
          strokeDasharray="3 8"
          opacity={p === 0.5 ? 0.6 : 0.4}
        />
      ))}
      {sorted.map((n) => {
        const cx = n.x * W;
        const cy = n.y * (H - 100) + 40;
        const r = lm.nodeRadius(n.influence, 1.6, 32);
        return (
          <g key={n.id}>
            {n.influence > 0.3 && <circle className="lm-live__node-shadow" cx={cx} cy={cy + 1.6} r={r * 1.02} fill="#000" opacity="0.08" />}
            <circle className="lm-live__node" cx={cx} cy={cy} r={r} fill={n.color} opacity="0.92" />
          </g>
        );
      })}
    </svg>
  );
}

// --------------------------------------------------------------------
// ActionTypeBadge
// --------------------------------------------------------------------
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

// --------------------------------------------------------------------
// AgentChip
// --------------------------------------------------------------------
function AgentChip({ agent, size = 'sm' }: { agent?: Agent; size?: 'sm' | 'lg' }) {
  if (!agent) return null;
  const role = lm.ROLE_BY_ID[agent.role];
  return (
    <span className={`lm-live__agent lm-live__agent--${size}`}>
      {agent.kind === 'anchor' && agent.avatar ? (
        <AvatarSVG roleId={agent.role} pose={agent.avatar.pose} prop={agent.avatar.prop} expr={agent.avatar.expr} size={size === 'lg' ? 32 : 22} />
      ) : (
        <span
          className="lm-live__agent-dot"
          style={{
            background: role.color,
            width: size === 'lg' ? 'calc(22px * var(--scale))' : 'calc(14px * var(--scale))',
            height: size === 'lg' ? 'calc(22px * var(--scale))' : 'calc(14px * var(--scale))',
          }}
        />
      )}
      <span className="lm-live__agent-name">
        {agent.short || agent.name}
        {agent.kind === 'derived-viral' && <em className="lm-live__viral-tag">viral</em>}
      </span>
    </span>
  );
}

// --------------------------------------------------------------------
// ActionItem — memo 로 감싸서 새 액션이 들어와도 기존 39개는 리렌더 안 함.
// agents.byId 가 동일 reference 면 props shallow equal → skip.
// --------------------------------------------------------------------
const ActionItem = memo(function ActionItem({ action, agents }: { action: Action; agents: AgentRegistry }) {
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
});

// --------------------------------------------------------------------
// 메인 통계 계산.
// 액션 누적 카운트는 별도 state(ActionCounters)로 incremental 하게 유지 —
// 매 onAction 마다 liveActions 전체를 6번 filter 하던 비용 제거.
// liveActions 자체는 사이드바 표시용 최근 40개만 cap.
// --------------------------------------------------------------------
interface LiveStats {
  round: number;
  utterances: number;
  likes: number;
  reposts: number;
  citations: number;
  follows: number;
  feedSize: number;
}

interface ActionCounters {
  total: number;
  utterances: number;
  likes: number;
  reposts: number;
  citations: number;
  follows: number;
}

const ZERO_COUNTERS: ActionCounters = { total: 0, utterances: 0, likes: 0, reposts: 0, citations: 0, follows: 0 };

function bumpCounters(c: ActionCounters, type: ActionType): ActionCounters {
  const next = { ...c, total: c.total + 1 };
  if (type === 'CREATE_POST') next.utterances += 1;
  else if (type === 'QUOTE_POST') {
    next.utterances += 1;
    next.citations += 1;
  } else if (type === 'LIKE') next.likes += 1;
  else if (type === 'REPOST') next.reposts += 1;
  else if (type === 'FOLLOW') next.follows += 1;
  return next;
}

function countActions(actions: Action[]): ActionCounters {
  const c: ActionCounters = { ...ZERO_COUNTERS };
  for (const a of actions) {
    c.total += 1;
    if (a.type === 'CREATE_POST') c.utterances += 1;
    else if (a.type === 'QUOTE_POST') {
      c.utterances += 1;
      c.citations += 1;
    } else if (a.type === 'LIKE') c.likes += 1;
    else if (a.type === 'REPOST') c.reposts += 1;
    else if (a.type === 'FOLLOW') c.follows += 1;
  }
  return c;
}

function computeStats(counters: ActionCounters, round: number): LiveStats {
  return {
    round,
    utterances: counters.utterances,
    likes: counters.likes,
    reposts: counters.reposts,
    citations: counters.citations,
    follows: counters.follows,
    feedSize: counters.total,
  };
}

// --------------------------------------------------------------------
// LiveSidebar
// --------------------------------------------------------------------
function LiveSidebar({
  actions,
  agents,
  round,
  total,
  stats,
  onClose,
}: {
  actions: Action[];
  agents: AgentRegistry;
  round: number;
  total: number;
  stats: LiveStats;
  onClose: () => void;
}) {
  // actions 는 이미 상위에서 최근 40개로 cap 됨 — filter/slice 없이 reverse 만.
  // round dep 제거 → 라운드 진행만으로 재계산 안 됨.
  const recent = useMemo(() => [...actions].reverse(), [actions]);

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

      {/* STATS PANEL — 백엔드가 직접 카운트하는 실수치만 표시 */}
      <section className="lm-live__stats-grid">
        <div className="lm-live__stat">
          <span className="lm-live__stat-k">라운드</span>
          <span className="lm-live__stat-v">
            {stats.round} <small>/ {total}</small>
          </span>
        </div>
        <div className="lm-live__stat">
          <span className="lm-live__stat-k">피드 크기</span>
          <span className="lm-live__stat-v">{stats.feedSize.toLocaleString()}</span>
        </div>
        <div className="lm-live__stat">
          <span className="lm-live__stat-k">발언</span>
          <span className="lm-live__stat-v">{stats.utterances.toLocaleString()}</span>
        </div>
        <div className="lm-live__stat">
          <span className="lm-live__stat-k">호응</span>
          <span className="lm-live__stat-v">{stats.likes.toLocaleString()}</span>
        </div>
        <div className="lm-live__stat">
          <span className="lm-live__stat-k">전파</span>
          <span className="lm-live__stat-v">{stats.reposts.toLocaleString()}</span>
        </div>
        <div className="lm-live__stat">
          <span className="lm-live__stat-k">인용</span>
          <span className="lm-live__stat-v">{stats.citations.toLocaleString()}</span>
        </div>
        <div className="lm-live__stat">
          <span className="lm-live__stat-k">팔로우 변화</span>
          <span className="lm-live__stat-v">+{stats.follows.toLocaleString()}</span>
        </div>
      </section>

      {/* ACTIVITY FEED */}
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

// --------------------------------------------------------------------
// 단계 텍스트
// --------------------------------------------------------------------
function liveStatus(round: number, total: number) {
  const settle = round / total;
  if (settle < 0.05) return { tag: '오프닝', text: '광장이 열렸어요. 인격들이 들어오고 있어요.' };
  if (settle < 0.3) return { tag: '입장', text: '아직 누가 어디로 갈지 정해지지 않았어요.' };
  if (settle < 0.55) return { tag: '진영 형성', text: '진영이 잡히고 있어요. 사람들이 옆자리를 찾아요.' };
  if (settle < 0.8) return { tag: '화제 부상', text: '몇몇 발언이 화제를 모으고 있어요. 노드가 커져요.' };
  if (settle < 0.98) return { tag: '수렴', text: '인격들이 자리를 잡고 있어요. 광장이 곧 닫혀요.' };
  return { tag: '종료 임박', text: '광장이 닫히고 있어요. 결과를 정리할게요.' };
}

// --------------------------------------------------------------------
// ScreenLive — 메인
// --------------------------------------------------------------------
export default function Live() {
  const { plazaId } = useParams<{ plazaId: string }>();
  const go = useScreenNav(plazaId);

  const [rawAgents, setRawAgents] = useState<PlazaAgentItem[]>([]);
  const [layoutAgents, setLayoutAgents] = useState<PlazaLayoutAgentItem[]>([]);

  // SSE 구동 상태.
  const [round, setRound] = useState(0);
  const [total, setTotal] = useState(50);
  const [phaseStatus, setPhaseStatus] = useState<PlazaStatus>('pending');
  // liveActions 는 사이드바 표시용 최근 40개만. 누적 카운트는 counters 로 분리.
  const [liveActions, setLiveActions] = useState<Action[]>([]);
  const [counters, setCounters] = useState<ActionCounters>(ZERO_COUNTERS);
  const [sidebarOpen, setSidebarOpen] = useState(true);

  // /agents 한 번만 — 광장 ID 고정이라 갱신 불필요.
  useEffect(() => {
    if (!plazaId) return;
    let cancelled = false;
    api.getAgents(plazaId)
      .then((res) => { if (!cancelled) setRawAgents(res.agents); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [plazaId]);

  // /layout 은 sim 진행 중엔 ready=false 라 status 가 composing/completed 로 바뀔 때 한 번 더.
  useEffect(() => {
    if (!plazaId) return;
    let cancelled = false;
    api.getLayout(plazaId)
      .then((res) => { if (!cancelled && res.ready) setLayoutAgents(res.agents); })
      .catch(() => {});
    return () => { cancelled = true; };
  }, [plazaId, phaseStatus]);

  // 사이드바 피드에 필요한 agent_id → name/role 매핑.
  const agents = useMemo<AgentRegistry>(() => {
    const list: Agent[] = rawAgents.map((a) => ({
      id: a.id,
      name: a.name,
      short: a.name,
      role: mapBackendRoleToRoleId(a.role),
      kind: 'anchor',
      avatar: avatarFromSeed(a.avatar_seed),
    }));
    return { list, byId: Object.fromEntries(list.map((a) => [a.id, a])) };
  }, [rawAgents]);

  const nodes = useMemo<PlazaNode[]>(() => buildLiveNodes(rawAgents, layoutAgents), [rawAgents, layoutAgents]);

  // SSE — progress / status / action / actions_snapshot.
  useEffect(() => {
    if (!plazaId) return;
    // 백엔드 ActionType ('LIKE_POST') 와 frontend mock ActionType ('LIKE') 의 wire
    // 차이를 여기서 정규화. ACTION_LABELS / computeStats 가 mock 키만 보므로
    // SSE 의 'LIKE_POST' 를 매핑 안 하면 ActionItem 렌더에서 undefined.tone throw.
    const toAction = (e: PlazaActionEvent): Action => ({
      round: e.round_num,
      agentId: e.agent_id,
      type: (e.type === 'LIKE_POST' ? 'LIKE' : e.type) as ActionType,
      content: e.content ?? undefined,
      targetId: e.target_agent_id ?? undefined,
    });
    const stream = api.streamPlazaEvents(plazaId, {
      onProgress: (e) => {
        setRound(e.rounds_done);
        setTotal(e.rounds_total);
      },
      onStatus: (e) => {
        setPhaseStatus(e.status);
        setRound(e.rounds_done);
        setTotal(e.rounds_total);
      },
      onAction: (e) => {
        const act = toAction(e);
        setLiveActions((prev) => (prev.length >= 40 ? [...prev.slice(1), act] : [...prev, act]));
        setCounters((c) => bumpCounters(c, act.type));
      },
      onActionsSnapshot: (e) => {
        const all = e.actions.map(toAction);
        setLiveActions(all.slice(-40));
        setCounters(countActions(all));
      },
    });
    return () => stream.close();
  }, [plazaId]);

  // composing 은 sim 자체는 끝났으니 progress 100% 강제, status 텍스트만 갱신.
  const progress = phaseStatus === 'composing' || phaseStatus === 'completed' ? 1 : round / Math.max(total, 1);
  const settle = progress;
  const status = phaseStatus === 'composing'
    ? { tag: '보고서 합성중', text: 'LLM 이 결과 보고서를 정리하고 있어요.' }
    : phaseStatus === 'completed'
    ? { tag: '광장 종료', text: '결과를 확인하세요.' }
    : liveStatus(round, total);
  const isCompleted = phaseStatus === 'completed';
  const stats = useMemo(() => computeStats(counters, round), [counters, round]);

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
              <Button kind="secondary" onClick={() => setSidebarOpen(true)}>
                활동 피드 열기
              </Button>
            )}
          </div>
        </header>

        <div className="lm-live__canvas">
          {rawAgents.length === 0 ? (
            <div className="lm-live__canvas-empty">에이전트 정보를 불러오는 중입니다.</div>
          ) : nodes.length === 0 ? (
            <div className="lm-live__canvas-empty">광장 좌표를 계산 중입니다. 시뮬레이션이 끝나면 표시돼요.</div>
          ) : (
            <>
              <LivePlaza nodes={nodes} />
              <div className="lm-live__canvas-axis">
                <span>← 비판적</span>
                <span>중립</span>
                <span>우호적 →</span>
              </div>
            </>
          )}
        </div>

        <footer className="lm-live__foot">
          <div className="lm-live__progress">
            <div className="lm-live__progress-bar" style={{ width: `${settle * 100}%` }} />
            {[...Array(total + 1).keys()]
              .filter((i) => i % 10 === 0)
              .map((i) => (
                <div key={i} className="lm-live__progress-tick" style={{ left: `${(i / total) * 100}%` }}>
                  <span>R{i}</span>
                </div>
              ))}
          </div>
          <div className="lm-live__foot-actions">
            <Button
              kind="primary"
              onClick={() => go('plaza')}
              trailing={<ArrowGlyph dir="right" />}
              disabled={!isCompleted}
            >
              {isCompleted
                ? '결과 광장 보기'
                : phaseStatus === 'composing'
                ? '보고서 합성중…'
                : '광장이 닫히면 결과로'}
            </Button>
          </div>
        </footer>
      </div>

      {sidebarOpen && (
        <LiveSidebar
          actions={liveActions}
          agents={agents}
          round={round}
          total={total}
          stats={stats}
          onClose={() => setSidebarOpen(false)}
        />
      )}
    </div>
  );
}
