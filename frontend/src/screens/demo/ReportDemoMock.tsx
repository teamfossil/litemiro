// =====================================================================
// 데모 결과 리포트 (Phase 6) — production Report.tsx 와 동일한 섹션 구조.
// 백엔드 대신 mock 데이터로 채우되, 컴포넌트 셰이프·className·순서는 일치.
// 섹션: 전체 요약(MiniPlaza) → 행동 → 토픽 → 라운드별 변화 → 영향력 → 소셜 → 비용 → 보고서 본문
// =====================================================================

import { useMemo, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import { lm } from '@/data/mock';
import type { PlazaNode } from '@/data/types';
import { Button, Stat, ArrowGlyph } from '@/components/atoms';

// --------------------------------------------------------------------
// Mock 데이터 — production categories / time_series / network_metrics 셰이프와 일치.
// --------------------------------------------------------------------

// 행동 분포 (production ActionDistribution 시그니처 일치)
const MOCK_ACTION_RATIOS: Record<string, number> = {
  CREATE_POST:  0.18,
  LIKE_POST:    0.34,
  REPOST:       0.22,
  QUOTE_POST:   0.12,
  FOLLOW:       0.10,
  DO_NOTHING:   0.04,
};
const MOCK_ACTION_TOTAL = 18_420;
const MOCK_N_ROUNDS = 50;
const MOCK_N_AGENTS = 300;
const MOCK_TOKENS   = 482_000;
const MOCK_N_EVENTS = 21_340;

// QA 메트릭 (production CostPanel qa 파라미터 셰이프)
const MOCK_QA: Record<string, number> = {
  action_entropy_normalized:       0.82,
  follow_clustering_coefficient:   0.34,
  content_word_entropy_normalized: 0.71,
};

// 라운드별 시계열 (production RoundSeriesChart SeriesPoint 셰이프 일치)
interface SeriesPoint {
  round_num: number;
  n_actions: number;
  n_active_agents: number;
  do_nothing_ratio: number;
}
const MOCK_SERIES: SeriesPoint[] = (() => {
  const rng = lm.mulberry32(7);
  const out: SeriesPoint[] = [];
  for (let r = 1; r <= MOCK_N_ROUNDS; r++) {
    const ramp = Math.min(1, r / 10);
    const tail = Math.max(0, 1 - (r - 38) / 12);
    const intensity = ramp * Math.min(1, tail) * (0.7 + 0.3 * rng());
    const n_actions = Math.round(40 + intensity * 340);
    const n_active_agents = Math.round(MOCK_N_AGENTS * (0.55 + intensity * 0.4));
    const do_nothing_ratio = lm.clamp(0.18 - intensity * 0.12 + (rng() - 0.5) * 0.04, 0.04, 0.35);
    out.push({ round_num: r, n_actions, n_active_agents, do_nothing_ratio });
  }
  return out;
})();

// 네트워크 (top_followed — production 영향력 섹션)
const MOCK_TOP_FOLLOWED = [
  { agent_id: '최영민 기자',    follows_received: 218 },
  { agent_id: '정세훈 의원',    follows_received: 184 },
  { agent_id: '한지영 칼럼니스트', follows_received: 171 },
  { agent_id: '시민·익명 #47', follows_received: 147 },
  { agent_id: '박서경 교수',    follows_received: 124 },
  { agent_id: '전국노동연대',   follows_received: 108 },
  { agent_id: '김민준',        follows_received:  82 },
  { agent_id: '이수빈',        follows_received:  71 },
  { agent_id: '박지훈',        follows_received:  64 },
  { agent_id: '최민서',        follows_received:  58 },
];
const MOCK_N_FOLLOW_EVENTS = 2_310;

// 토픽 게시물 샘플 (production 토픽 섹션)
const MOCK_TOPIC_POSTS = 3_624;
const MOCK_TOP_POSTERS = [
  { agent_id: '최영민 기자',    posts: 147 },
  { agent_id: '정세훈 의원',    posts: 122 },
  { agent_id: '한지영 칼럼니스트', posts: 118 },
  { agent_id: '박서경 교수',    posts:  94 },
  { agent_id: '시민·익명 #47', posts:  89 },
];
const MOCK_TOPIC_SAMPLES = [
  { round_num: 14, agent_id: '시민·익명 #47', content: '우리 동네 어린이집 보육교사 친구는 그냥, 하루만 더 쉬어도 사람답게 산다고 그래요.' },
  { round_num: 23, agent_id: '최영민 기자',    content: '통계는 평균을 보지만 시급제 노동자는 평균 위에 살지 않습니다.' },
  { round_num: 31, agent_id: '정세훈 의원',    content: '발의안의 핵심은 강제가 아니라 권리 명시입니다. 근로자가 선택할 수 있어야 합니다.' },
];


// --------------------------------------------------------------------
// SectionShell — production Report.tsx 와 동일 셰이프
// --------------------------------------------------------------------
function ReportSection({
  id, num, title, sub, actions, children,
}: {
  id: string; num: string; title: ReactNode;
  sub?: ReactNode; actions?: ReactNode; children: ReactNode;
}) {
  return (
    <section id={id} className="lm-rep__section">
      <header className="lm-rep__sec-head">
        <div className="lm-rep__sec-head-left">
          <span className="lm-rep__sec-num">{num}</span>
          <div>
            <h2 className="lm-rep__sec-title">{title}</h2>
            {sub && <p className="lm-rep__sec-sub">{sub}</p>}
          </div>
        </div>
        {actions && <div className="lm-rep__sec-actions">{actions}</div>}
      </header>
      <div className="lm-rep__sec-body">{children}</div>
    </section>
  );
}

// --------------------------------------------------------------------
// MiniPlaza — production Report.tsx 와 동일
// --------------------------------------------------------------------
function MiniPlaza({ nodes }: { nodes: PlazaNode[] }) {
  const W = 1680, H = 920;
  const sorted = useMemo(() => [...nodes].sort((a, b) => a.influence - b.influence), [nodes]);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="lm-rep__plaza-svg" role="img">
      {[0.25, 0.5, 0.75].map((p, i) => (
        <line key={i} x1={W * p} x2={W * p} y1={40} y2={H - 60}
          stroke="#C9C1AD" strokeWidth="1" strokeDasharray="3 8"
          opacity={p === 0.5 ? 0.6 : 0.4} />
      ))}
      {sorted.map((n) => {
        const cx = n.x * W;
        const cy = n.y * (H - 100) + 40;
        const r = lm.nodeRadius(n.influence, 1.6, 32);
        return (
          <g key={n.id}>
            {n.influence > 0.3 && <circle cx={cx} cy={cy + 1.6} r={r * 1.02} fill="#000" opacity="0.08" />}
            <circle cx={cx} cy={cy} r={r} fill={n.color} opacity="0.92" />
          </g>
        );
      })}
    </svg>
  );
}

// --------------------------------------------------------------------
// ActionDistribution — production Report.tsx 의 셰이프/className 일치
// --------------------------------------------------------------------
const ACTION_META: Record<string, { label: string; tone: string }> = {
  CREATE_POST:  { label: '발언',   tone: 'create' },
  LIKE_POST:    { label: '호응',   tone: 'like'   },
  REPOST:       { label: '전파',   tone: 'repost' },
  QUOTE_POST:   { label: '인용',   tone: 'quote'  },
  FOLLOW:       { label: '팔로우', tone: 'follow' },
  DO_NOTHING:   { label: '관망',   tone: 'idle'   },
};

function ActionDistribution({ ratios, total, rounds }: { ratios: Record<string, number>; total: number; rounds: number }) {
  const rows = Object.entries(ratios)
    .filter(([type]) => ACTION_META[type])
    .sort((a, b) => b[1] - a[1]);
  return (
    <div className="lm-rep__actions">
      {rows.map(([type, pct]) => {
        const meta = ACTION_META[type];
        const count = Math.round(total * pct);
        return (
          <div key={type} className="lm-rep__action-row">
            <span className={`lm-rep__action-tag lm-rep__action-tag--${meta.tone}`}>{meta.label}</span>
            <div className="lm-rep__action-bar">
              <div className={`lm-rep__action-bar-fill lm-rep__action-bar-fill--${meta.tone}`}
                style={{ width: `${pct * 100}%` }} />
            </div>
            <span className="lm-rep__action-pct">{(pct * 100).toFixed(0)}%</span>
            <span className="lm-rep__action-count">{count.toLocaleString()}건</span>
          </div>
        );
      })}
      <div className="lm-rep__action-foot">
        총 행동 {total.toLocaleString()}건{rounds > 0 ? ` · 라운드당 평균 ${(total / rounds).toFixed(1)}건` : ''}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// RoundSeriesChart — production Report.tsx 와 동일 셰이프 (n_actions / n_active_agents)
// --------------------------------------------------------------------
function RoundSeriesChart({ data }: { data: SeriesPoint[] }) {
  const W = 1400, H = 360;
  const pad = { l: 56, r: 24, t: 24, b: 40 };
  const w = W - pad.l - pad.r;
  const h = H - pad.t - pad.b;
  const total = data.length;
  if (total === 0) return <div className="lm-rep__empty">라운드별 활동이 기록되지 않았습니다.</div>;

  const maxActions = Math.max(...data.map((d) => d.n_actions), 1);
  const maxActive  = Math.max(...data.map((d) => d.n_active_agents), 1);
  const x = (i: number) => pad.l + (total > 1 ? (i / (total - 1)) * w : w / 2);

  const linePath = (key: 'n_actions' | 'n_active_agents', max: number) =>
    data.map((d, i) => {
      const xi = x(i);
      const yi = pad.t + (1 - d[key] / max) * h;
      return `${i === 0 ? 'M' : 'L'} ${xi} ${yi}`;
    }).join(' ');

  const ticks = [1, 10, 20, 30, 40, 50].filter((r) => r <= data[data.length - 1].round_num);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="lm-rep__series-svg" role="img"
      aria-label="라운드별 활동 및 활성 에이전트">
      <path d={linePath('n_actions', maxActions)} fill="none" stroke="#1A1813" strokeWidth="1.6" strokeLinecap="round" />
      <path d={linePath('n_active_agents', maxActive)} fill="none" stroke="#4F7591" strokeWidth="1.4" strokeDasharray="4 4" strokeLinecap="round" />
      {ticks.map((r) => {
        const i = data.findIndex((d) => d.round_num === r);
        if (i < 0) return null;
        return (
          <text key={r} x={x(i)} y={H - 14} fontFamily="IBM Plex Mono" fontSize="12"
            fill="#8E8674" textAnchor="middle">R{r}</text>
        );
      })}
      <text x={20} y={pad.t + 12} fontFamily="IBM Plex Mono" fontSize="12" fill="#8E8674">활동수</text>
      <text x={20} y={H - pad.b - 4} fontFamily="IBM Plex Mono" fontSize="12" fill="#8E8674">0</text>
    </svg>
  );
}

// --------------------------------------------------------------------
// CostPanel — production Report.tsx 의 CostPanel 셰이프 일치
// --------------------------------------------------------------------
function CostPanel({ tokens, qa, nAgents, nRounds }: {
  tokens: number; qa: Record<string, number>; nAgents: number; nRounds: number;
}) {
  const fmtRatio = (v?: number) => (v == null ? '—' : `${(v * 100).toFixed(1)}%`);
  return (
    <div className="lm-rep__cost-grid">
      <div className="lm-rep__cost-cell">
        <div className="lm-rep__cost-k">토큰 사용량</div>
        <div className="lm-rep__cost-v">{tokens.toLocaleString()}</div>
        <div className="lm-rep__cost-sub">in + out 누적</div>
      </div>
      <div className="lm-rep__cost-cell">
        <div className="lm-rep__cost-k">시뮬레이션 규모</div>
        <div className="lm-rep__cost-v">{nAgents.toLocaleString()}<small>명</small></div>
        <div className="lm-rep__cost-sub">{nRounds} 라운드</div>
      </div>
      <div className="lm-rep__cost-cell">
        <div className="lm-rep__cost-k">행동 다양성</div>
        <div className="lm-rep__cost-v">{fmtRatio(qa.action_entropy_normalized)}</div>
        <div className="lm-rep__cost-sub">정규화 엔트로피</div>
      </div>
      <div className="lm-rep__cost-cell">
        <div className="lm-rep__cost-k">팔로우 군집도</div>
        <div className="lm-rep__cost-v">{fmtRatio(qa.follow_clustering_coefficient)}</div>
        <div className="lm-rep__cost-sub">clustering coefficient</div>
      </div>
      <div className="lm-rep__cost-cell">
        <div className="lm-rep__cost-k">표현 다양성</div>
        <div className="lm-rep__cost-v">{fmtRatio(qa.content_word_entropy_normalized)}</div>
        <div className="lm-rep__cost-sub">콘텐츠 단어 엔트로피</div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// ReportDemoMock — 메인. production Report 와 동일 섹션 순서·className.
// --------------------------------------------------------------------
export default function ReportDemoMock() {
  const navigate = useNavigate();
  const go = (target: 'plaza' | 'live' | 'casting' | 'report' | 'landing' | 'seed') =>
    navigate(target === 'landing' ? '/' : `/demo/${target}`);
  const nodes = useMemo(() => lm.generatePlaza({ seed: 42, n: 300 }), []);
  const handlePrintPdf = () => window.print();

  return (
    <div className="lm-rep">
      <div className="lm-rep__pad">

        {/* HEADER — production 과 동일 구조 */}
        <header className="lm-rep__head">
          <div className="lm-rep__head-left">
            <div className="lm-rep__head-eyebrow">
              Phase 6 · 결과 리포트 · R{MOCK_N_ROUNDS}/{MOCK_N_ROUNDS}
            </div>
            <h1 className="lm-rep__head-title">주 4일제 도입 시뮬레이션 리포트</h1>
            <div className="lm-rep__head-meta">
              <span>{lm.SEED.title}</span>
              <span>광장 ID · {lm.SEED.id}</span>
              <span>{MOCK_N_AGENTS}명 · {MOCK_N_ROUNDS}라운드</span>
            </div>
          </div>
          <div className="lm-rep__head-actions">
            <Button kind="ghost" onClick={() => go('plaza')}>광장으로</Button>
            <Button kind="secondary" onClick={handlePrintPdf}>PDF</Button>
          </div>
        </header>

        {/* PREDICTION HERO — production: ReactMarkdown. 데모: pre-rendered markdown prose. */}
        <section className="lm-rep__hero">
          <div className="lm-rep__hero-tag">PREDICTION · 핵심 예측</div>
          <div className="lm-rep__hero-markdown">
            <p className="lm-rep__hero-text">
              주 4일제는 단기 도입은 어렵지만, <strong>시범사업 결과를 거쳐 2~3년 내 점진 도입</strong>될 가능성이 높습니다.
            </p>
            <div className="lm-rep__hero-meta">
              <span>신뢰도 · <b>medium-high</b></span>
            </div>
          </div>
        </section>

        {/* 01 전체 요약 */}
        <ReportSection id="summary" num="01" title="전체 요약"
          sub="시뮬레이션 핵심 결과를 한눈에 정리합니다.">
          <div className="lm-rep__sum-grid">
            <div className="lm-rep__sum-plaza">
              <div className="lm-rep__sec-card-head">
                <h3>종료 광장</h3>
                <Button kind="link" onClick={() => go('plaza')}>
                  드릴인 <ArrowGlyph dir="right" size={8} />
                </Button>
              </div>
              <div className="lm-rep__sum-plaza-vis">
                <MiniPlaza nodes={nodes} />
                <div className="lm-rep__sum-plaza-axis">
                  <span>← 진보</span>
                  <span>중립</span>
                  <span>보수 →</span>
                </div>
              </div>
            </div>
            <div className="lm-rep__sum-side">
              <div className="lm-rep__sum-stats">
                <Stat label="참여 인격"   value={MOCK_N_AGENTS} />
                <Stat label="라운드"      value={`${MOCK_N_ROUNDS}/${MOCK_N_ROUNDS}`} />
                <Stat label="총 이벤트"   value={MOCK_N_EVENTS.toLocaleString()} />
                <Stat label="총 행동"     value={MOCK_ACTION_TOTAL.toLocaleString()} />
                <Stat label="게시물"      value={MOCK_TOPIC_POSTS.toLocaleString()} />
                <Stat label="팔로우 이벤트" value={MOCK_N_FOLLOW_EVENTS.toLocaleString()} />
              </div>
            </div>
          </div>
        </ReportSection>

        {/* 02 행동 분석 */}
        <ReportSection id="behavior" num="02" title="행동 분석"
          sub="게시글·좋아요·리포스트·인용·팔로우 비율로 광장의 활동 패턴을 봅니다.">
          <ActionDistribution ratios={MOCK_ACTION_RATIOS} total={MOCK_ACTION_TOTAL} rounds={MOCK_N_ROUNDS} />
        </ReportSection>

        {/* 03 토픽 분석 */}
        <ReportSection id="topic" num="03" title="토픽 분석"
          sub="라운드별 게시물 흐름과 가장 활발하게 발언한 에이전트.">
          <div className="lm-rep__topics">
            <div className="lm-rep__topic-row">
              <span className="lm-rep__topic-name">총 게시물</span>
              <span className="lm-rep__topic-count">{MOCK_TOPIC_POSTS.toLocaleString()}</span>
            </div>
            {MOCK_TOP_POSTERS.map((p) => (
              <div key={p.agent_id} className="lm-rep__topic-row">
                <span className="lm-rep__topic-name">{p.agent_id}</span>
                <span className="lm-rep__topic-count">{p.posts} 건</span>
              </div>
            ))}
            {MOCK_TOPIC_SAMPLES.map((s, i) => (
              <div key={i} className="lm-rep__topic-sample">
                <div className="lm-rep__topic-sample-head">R{s.round_num} · {s.agent_id}</div>
                <div className="lm-rep__topic-sample-body">{s.content}</div>
              </div>
            ))}
          </div>
        </ReportSection>

        {/* 04 라운드별 변화 */}
        <ReportSection id="time" num="04" title="라운드별 변화"
          sub="시간에 따라 활동 수와 활성 에이전트가 어떻게 변했는지.">
          <div className="lm-rep__series-card">
            <RoundSeriesChart data={MOCK_SERIES} />
            <div className="lm-rep__series-legend">
              <span><i className="lm-rep__series-line" /> 활동 수</span>
              <span><i className="lm-rep__series-line" style={{ borderTopStyle: 'dashed' }} /> 활성 에이전트</span>
            </div>
          </div>
        </ReportSection>

        {/* 05 영향력 분석 */}
        <ReportSection id="influence" num="05" title="영향력 분석"
          sub="팔로우를 가장 많이 받은 에이전트 / 가장 활발하게 발언한 에이전트.">
          <div className="lm-rep__inf">
            <header className="lm-rep__inf-head">
              <span className="lm-rep__inf-col-rank">순위</span>
              <span className="lm-rep__inf-col-who">에이전트</span>
              <span className="lm-rep__inf-col-fol">팔로워 +</span>
            </header>
            {MOCK_TOP_FOLLOWED.map((row, i) => (
              <div key={row.agent_id} className="lm-rep__inf-row">
                <span className="lm-rep__inf-col-rank">{String(i + 1).padStart(2, '0')}</span>
                <span className="lm-rep__inf-col-who">{row.agent_id}</span>
                <span className="lm-rep__inf-col-fol">+{row.follows_received}</span>
              </div>
            ))}
          </div>
        </ReportSection>

        {/* 06 소셜 그래프 */}
        <ReportSection id="social" num="06" title="소셜 그래프 분석"
          sub="광장에서 발생한 팔로우 관계 요약.">
          <div className="lm-rep__social-metrics">
            <Stat label="총 팔로우 이벤트" value={MOCK_N_FOLLOW_EVENTS.toLocaleString()} align="left" />
            <Stat label="피팔로우 상위"    value={MOCK_TOP_FOLLOWED.length}               align="left" />
            <Stat label="팔로우 상위"      value={MOCK_TOP_FOLLOWED.length}               align="left" />
          </div>
        </ReportSection>

        {/* 07 비용·품질 */}
        <ReportSection id="cost" num="07" title="비용 · 품질 지표"
          sub="토큰 사용량과 시뮬레이션 품질 메트릭.">
          <CostPanel tokens={MOCK_TOKENS} qa={MOCK_QA} nAgents={MOCK_N_AGENTS} nRounds={MOCK_N_ROUNDS} />
        </ReportSection>

        {/* 보고서 본문 — production 은 ReactMarkdown, 데모는 hero-markdown prose 스타일 재사용 */}
        <ReportSection id="report" num="08" title="보고서 본문"
          sub="시뮬레이션 결과 전체 서술. 실제 제품에서는 LLM 이 합성한 markdown 이 여기 들어갑니다.">
          <div className="lm-rep__hero-markdown">
            <h2>핵심 예측</h2>
            <p>주 4일제는 단기 도입은 어렵지만, <strong>시범사업 결과를 거쳐 2~3년 내 점진 도입</strong>될 가능성이 높습니다.</p>
            <p>신뢰도 · <strong>medium-high</strong></p>
            <h3>추진 신호</h3>
            <ul>
              <li>시범사업 데이터가 공개되면 중립 클러스터가 비판적으로 기울 신호가 보입니다.</li>
              <li>돌봄·시급제 영역의 사례 노출이 여론에 가장 큰 영향을 줄 것으로 예상됩니다.</li>
            </ul>
            <h3>저항 신호</h3>
            <ul>
              <li>산업계 부담 분담 구조 합의 없이는 단기 입법은 어렵습니다.</li>
            </ul>
            <h3>후속 권장 액션</h3>
            <ol>
              <li>시범사업 결과 보고서 1차 공개 시점에 추가 시뮬레이션 권장</li>
              <li>5인 미만 사업장 보호 조항을 변수에 포함한 후속 시드 실행</li>
              <li>미디어 진영 앵커 3명의 영향력 패턴을 별도 분석</li>
            </ol>
          </div>
        </ReportSection>

        {/* FOOTER */}
        <footer className="lm-rep__foot">
          <div className="lm-rep__foot-meta">
            <span>광장 상태 · completed</span>
          </div>
        </footer>

      </div>
    </div>
  );
}
