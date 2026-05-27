// =====================================================================
// 결과 리포트 (Phase 6) — Result Report
// 파이프라인:
//   Phase 2 산출물 (JSONL + 최종 상태 JSON)
//     → DataAggregator → PatternAnalyzer
//     → ReportComposer → ReportFormatter
//     → Markdown/PDF 보고서
// UI는 ReportFormatter 단계의 산출물.
// 섹션: 전체 요약 → 행동 → 토픽 → 라운드별 변화 → 영향력 → 소셜그래프 → 비용 → 결론.
// (screen-report.jsx → ES 모듈 + 타입. 별칭 훅 → 표준 훅, window.LM → lm)
// =====================================================================

import { useEffect, useMemo, useState, type ReactNode } from 'react';
import { useParams } from 'react-router-dom';
import ReactMarkdown from 'react-markdown';
import { lm } from '@/data/mock';
import type { ActionType, PlazaNode, ReportData, Stance } from '@/data/types';
import { AvatarSVG, RoleSwatch, Button, Stat, ArrowGlyph } from '@/components/atoms';
import { useScreenNav } from '@/lib/nav';
import { api, type PlazaReportResponse } from '@/api/client';

// --------------------------------------------------------------------
// 모킹 데이터 — 실 제품에서는 DataAggregator/PatternAnalyzer 출력으로 교체.
// 시드 RNG로 일관된 숫자.
// --------------------------------------------------------------------
const REPORT_DATA: ReportData = (() => {
  const total = lm.ROUND_META.total;
  const counts = lm.ROUND_META.counts;
  const rng = lm.mulberry32(7);

  // 1) 행동 비율
  const actions: ReportData['actions'] = {
    CREATE_POST: 0.18,
    LIKE: 0.36,
    REPOST: 0.22,
    QUOTE_POST: 0.12,
    FOLLOW: 0.12,
  };

  // 2) 토픽 — 시드 keywords + 가중치 + 진영 분포
  const topics: ReportData['topics'] = [
    { id: 'tr', name: '#트랜지션', hits: 412, dominant: 'con', share: { pro: 0.22, mid: 0.18, con: 0.6 } },
    { id: 'lab', name: '#근로기준법', hits: 388, dominant: 'pro', share: { pro: 0.48, mid: 0.22, con: 0.3 } },
    { id: 'w4', name: '#주4일제', hits: 360, dominant: 'mid', share: { pro: 0.36, mid: 0.3, con: 0.34 } },
    { id: 'ca', name: '#보육', hits: 282, dominant: 'pro', share: { pro: 0.62, mid: 0.2, con: 0.18 } },
    { id: 'pi', name: '#시범사업', hits: 240, dominant: 'pro', share: { pro: 0.54, mid: 0.24, con: 0.22 } },
    { id: 'oe', name: '#OECD', hits: 186, dominant: 'con', share: { pro: 0.3, mid: 0.18, con: 0.52 } },
    { id: 'cr', name: '#돌봄', hits: 162, dominant: 'pro', share: { pro: 0.66, mid: 0.18, con: 0.16 } },
  ];

  // 3) 라운드별 시계열
  // 발화량 / 진영 비율 / 영향력 누적
  const series: ReportData['series'] = [];
  for (let r = 1; r <= total; r++) {
    const ramp = Math.min(1, r / 10); // 첫 10R에 활발해짐
    const tail = Math.max(0, 1 - (r - 38) / 12); // R38 이후 감소
    const intensity = ramp * Math.min(1, tail) * (0.7 + 0.3 * rng());
    const u = Math.round(40 + intensity * 110);

    // 진영 비율은 천천히 안정화
    const settle = Math.min(1, r / 30);
    const pro = 0.42 + (0.38 - 0.42) * settle + (rng() - 0.5) * 0.04;
    const con = 0.32 + (0.39 - 0.32) * settle + (rng() - 0.5) * 0.04;
    const mid = 1 - pro - con;
    series.push({ round: r, utterances: u, pro, mid, con });
  }

  // 4) 영향력 앵커
  const anchors: ReportData['anchors'] = lm.ANCHORS.map((a) => ({
    ...a,
    citations: Math.round(a.baseInfluence * 124),
    propagations: Math.round(a.baseInfluence * 220),
    crossCites: Math.round(a.baseInfluence * 84),
    followerGain: Math.round(a.baseInfluence * 220) + 18,
  })).sort((x, y) => y.baseInfluence - x.baseInfluence);

  // 5) 소셜 그래프 변화
  const social: ReportData['social'] = {
    initialFollows: 1240,
    finalFollows: 1240 + Math.round(0.78 * 2410), // ≈ 3119
    newFollows: Math.round(0.78 * 2410),
    cliqueCount: 7,
    bridgeAgents: 3, // 진영 가로지른 사람
    rewireRate: 0.22, // 22%가 재배치
  };

  // 6) 비용
  const cost: ReportData['cost'] = {
    tokens: 482_000,
    calls: 1_624,
    fallbackPct: 2.4,
    latencyAvg: 1.18,
    krw: 1_240,
  };

  // 7) 결론 — 예측 문장
  const prediction: ReportData['prediction'] = {
    headline: '주 4일제는 단기 도입은 어렵지만, 시범사업 결과를 거쳐 2~3년 내 점진 도입될 가능성이 높습니다.',
    confidence: 'medium-high',
    bullets: [
      { kind: 'pro', text: '시범사업 데이터가 공개되면 중립 클러스터가 비판적으로 기울 신호가 보입니다.' },
      { kind: 'con', text: '산업계 부담 분담 구조 합의 없이는 단기 입법은 어렵습니다.' },
      { kind: 'note', text: '돌봄·시급제 영역의 사례 노출이 여론에 가장 큰 영향을 줄 것으로 예상됩니다.' },
    ],
    recommendations: [
      '시범사업 결과 보고서 1차 공개 시점에 추가 시뮬레이션 권장',
      '5인 미만 사업장 보호 조항을 변수에 포함한 후속 시드 실행',
      '미디어 진영 앵커 3명의 영향력 패턴을 별도 분석',
    ],
  };

  return { actions, topics, series, anchors, social, cost, prediction, total, counts };
})();

// --------------------------------------------------------------------
// SectionShell — 모든 섹션이 같은 헤딩 패턴
// --------------------------------------------------------------------
function ReportSection({
  id,
  num,
  title,
  sub,
  actions,
  children,
}: {
  id: string;
  num: string;
  title: ReactNode;
  sub?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
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
// MiniPlaza — 종료 광장 시그니처 (compact)
// --------------------------------------------------------------------
function MiniPlaza({ nodes }: { nodes: PlazaNode[] }) {
  const W = 1400,
    H = 700;
  const sorted = useMemo(() => [...nodes].sort((a, b) => a.influence - b.influence), [nodes]);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="lm-rep__plaza-svg" role="img">
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
          opacity={p === 0.5 ? 0.5 : 0.3}
        />
      ))}
      {sorted.map((n) => {
        const cx = n.x * W;
        const cy = n.y * (H - 100) + 30;
        const r = lm.nodeRadius(n.influence, 1.8, 28);
        return (
          <g key={n.id}>
            {n.influence > 0.35 && <circle cx={cx} cy={cy + 1.4} r={r * 1.02} fill="#000" opacity="0.08" />}
            <circle cx={cx} cy={cy} r={r} fill={n.color} opacity="0.92" />
            {n.anchor && (
              <circle cx={cx} cy={cy} r={r + 3} fill="none" stroke={n.color} strokeWidth="1" opacity="0.35" />
            )}
          </g>
        );
      })}
    </svg>
  );
}

// --------------------------------------------------------------------
// ActionDistribution — 5가지 행동 비율 (수평 막대)
// --------------------------------------------------------------------
function ActionDistribution() {
  const labels: Record<ActionType, { label: string; tone: string }> = {
    CREATE_POST: { label: '발언', tone: 'create' },
    LIKE: { label: '호응', tone: 'like' },
    REPOST: { label: '전파', tone: 'repost' },
    QUOTE_POST: { label: '인용', tone: 'quote' },
    FOLLOW: { label: '팔로우', tone: 'follow' },
  };
  const total = lm.ROUND_META.total;
  const utterances = lm.ROUND_META.counts.utterances;
  const totalActions = Math.round(utterances / (REPORT_DATA.actions.CREATE_POST + REPORT_DATA.actions.QUOTE_POST));

  return (
    <div className="lm-rep__actions">
      {(Object.entries(REPORT_DATA.actions) as [ActionType, number][]).map(([type, pct]) => {
        const meta = labels[type];
        const count = Math.round(totalActions * pct);
        return (
          <div key={type} className="lm-rep__action-row">
            <span className={`lm-rep__action-tag lm-rep__action-tag--${meta.tone}`}>{meta.label}</span>
            <div className="lm-rep__action-bar">
              <div
                className={`lm-rep__action-bar-fill lm-rep__action-bar-fill--${meta.tone}`}
                style={{ width: `${pct * 100}%` }}
              />
            </div>
            <span className="lm-rep__action-pct">{(pct * 100).toFixed(0)}%</span>
            <span className="lm-rep__action-count">{count.toLocaleString()}건</span>
          </div>
        );
      })}
      <div className="lm-rep__action-foot">
        총 행동 {totalActions.toLocaleString()}건 · 라운드당 평균 {Math.round(totalActions / total)}건
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// TopicChart — 키워드 빈도 + 진영 분포 (수평 스택)
// --------------------------------------------------------------------
function TopicChart() {
  const max = Math.max(...REPORT_DATA.topics.map((t) => t.hits));
  return (
    <div className="lm-rep__topics">
      {REPORT_DATA.topics.map((t) => (
        <div key={t.id} className="lm-rep__topic-row">
          <span className="lm-rep__topic-name">{t.name}</span>
          <span className="lm-rep__topic-count">{t.hits}</span>
          <div className="lm-rep__topic-bar">
            <div
              className="lm-rep__topic-seg lm-rep__topic-seg--pro"
              style={{ width: `${t.share.pro * (t.hits / max) * 100}%` }}
            />
            <div
              className="lm-rep__topic-seg lm-rep__topic-seg--mid"
              style={{ width: `${t.share.mid * (t.hits / max) * 100}%` }}
            />
            <div
              className="lm-rep__topic-seg lm-rep__topic-seg--con"
              style={{ width: `${t.share.con * (t.hits / max) * 100}%` }}
            />
          </div>
          <span className={`lm-rep__topic-dom lm-rep__topic-dom--${t.dominant}`}>
            {t.dominant === 'pro' ? '비판적 우세' : t.dominant === 'con' ? '우호적 우세' : '중립 우세'}
          </span>
        </div>
      ))}
      <div className="lm-rep__topic-legend">
        <span>
          <span className="lm-rep__legend-dot" style={{ background: '#4F7591' }} />
          비판적
        </span>
        <span>
          <span className="lm-rep__legend-dot" style={{ background: '#8B8170' }} />
          중립
        </span>
        <span>
          <span className="lm-rep__legend-dot" style={{ background: '#C77B4F' }} />
          우호적
        </span>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// RoundSeriesChart — 라운드별 발화량 + 진영 비율 영역 그래프
// --------------------------------------------------------------------
function RoundSeriesChart() {
  const data = REPORT_DATA.series;
  const W = 1400,
    H = 360;
  const pad = { l: 56, r: 24, t: 24, b: 40 };
  const w = W - pad.l - pad.r;
  const h = H - pad.t - pad.b;
  const total = data.length;
  const maxU = Math.max(...data.map((d) => d.utterances));

  // 진영 비율 영역 (적층)
  const x = (i: number) => pad.l + (i / (total - 1)) * w;
  const stackPath = (key: Stance) => {
    const upper: [number, number][] = [];
    const lower: [number, number][] = [];
    data.forEach((d, i) => {
      const xi = x(i);
      let top = 0,
        bot = 0;
      if (key === 'pro') {
        top = d.pro;
        bot = 0;
      } else if (key === 'mid') {
        top = d.pro + d.mid;
        bot = d.pro;
      } else if (key === 'con') {
        top = 1;
        bot = d.pro + d.mid;
      }
      upper.push([xi, pad.t + (1 - top) * h]);
      lower.push([xi, pad.t + (1 - bot) * h]);
    });
    const path = [
      `M ${upper[0][0]} ${upper[0][1]}`,
      ...upper.slice(1).map((p) => `L ${p[0]} ${p[1]}`),
      ...lower.reverse().map((p) => `L ${p[0]} ${p[1]}`),
      'Z',
    ].join(' ');
    return path;
  };

  // 발화량 라인 (위에 겹쳐)
  const linePath = data
    .map((d, i) => {
      const xi = x(i);
      const yi = pad.t + (1 - d.utterances / maxU) * h;
      return `${i === 0 ? 'M' : 'L'} ${xi} ${yi}`;
    })
    .join(' ');

  // R20, R30 변곡점
  const inflections = [20, 30];

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      className="lm-rep__series-svg"
      role="img"
      aria-label="라운드별 발화량 및 진영 비율"
    >
      {/* 진영 비율 stacked areas */}
      <path d={stackPath('pro')} fill="#4F7591" opacity="0.32" />
      <path d={stackPath('mid')} fill="#8B8170" opacity="0.32" />
      <path d={stackPath('con')} fill="#C77B4F" opacity="0.32" />

      {/* 발화량 라인 */}
      <path d={linePath} fill="none" stroke="#1A1813" strokeWidth="1.6" strokeLinecap="round" />

      {/* 변곡점 */}
      {inflections.map((r) => {
        const i = r - 1;
        const xi = x(i);
        return (
          <g key={r}>
            <line
              x1={xi}
              y1={pad.t}
              x2={xi}
              y2={H - pad.b}
              stroke="#1A1813"
              strokeWidth="1"
              strokeDasharray="3 4"
              opacity="0.5"
            />
            <text x={xi + 4} y={pad.t + 12} fontFamily="IBM Plex Mono" fontSize="12" fill="#5C5447">
              R{r} · 변곡
            </text>
          </g>
        );
      })}

      {/* X axis ticks */}
      {[1, 10, 20, 30, 40, 50].map((r) => {
        const i = r - 1;
        return (
          <text
            key={r}
            x={x(i)}
            y={H - 14}
            fontFamily="IBM Plex Mono"
            fontSize="12"
            fill="#8E8674"
            textAnchor="middle"
          >
            R{r}
          </text>
        );
      })}

      {/* Y axis label */}
      <text x={20} y={pad.t + 12} fontFamily="IBM Plex Mono" fontSize="12" fill="#8E8674">
        발화량
      </text>
      <text x={20} y={H - pad.b - 4} fontFamily="IBM Plex Mono" fontSize="12" fill="#8E8674">
        0
      </text>
    </svg>
  );
}

// --------------------------------------------------------------------
// InfluenceTable — 영향력 앵커 5명 표
// --------------------------------------------------------------------
function InfluenceTable() {
  return (
    <div className="lm-rep__inf">
      <header className="lm-rep__inf-head">
        <span className="lm-rep__inf-col-rank">순위</span>
        <span className="lm-rep__inf-col-who">에이전트</span>
        <span className="lm-rep__inf-col-score">화제성</span>
        <span className="lm-rep__inf-col-cite">교차 인용</span>
        <span className="lm-rep__inf-col-rep">전파</span>
        <span className="lm-rep__inf-col-fol">팔로워 +</span>
      </header>
      {REPORT_DATA.anchors.map((a, i) => {
        const role = lm.ROLE_BY_ID[a.role];
        return (
          <div key={a.id} className="lm-rep__inf-row">
            <span className="lm-rep__inf-col-rank">{String(i + 1).padStart(2, '0')}</span>
            <span className="lm-rep__inf-col-who">
              <AvatarSVG roleId={a.role} pose={a.avatar.pose} prop={a.avatar.prop} expr={a.avatar.expr} size={32} />
              <span className="lm-rep__inf-name">
                <b>
                  {a.name}
                  {!a.isOrg && ' ' + a.title}
                </b>
                <small>
                  <RoleSwatch roleId={a.role} size={6} /> {role.name}
                </small>
              </span>
            </span>
            <span className="lm-rep__inf-col-score">{Math.round(a.baseInfluence * 10000).toLocaleString()}</span>
            <span className="lm-rep__inf-col-cite">{a.crossCites}</span>
            <span className="lm-rep__inf-col-rep">{a.propagations}</span>
            <span className="lm-rep__inf-col-fol">+{a.followerGain}</span>
          </div>
        );
      })}
      <div className="lm-rep__inf-foot">
        상위 3명이 전체 교차 인용의 <b>62%</b>를 만들었어요.
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// SocialDelta — 소셜 그래프 변화 (Before/After)
// --------------------------------------------------------------------
function SocialDelta() {
  const { initialFollows, finalFollows, newFollows, cliqueCount, bridgeAgents, rewireRate } = REPORT_DATA.social;
  return (
    <div className="lm-rep__social">
      <div className="lm-rep__social-graph">
        <div className="lm-rep__social-side">
          <div className="lm-rep__social-tag">시드 직후 (R0)</div>
          <SocialMiniGraph stage="initial" />
          <div className="lm-rep__social-stat">팔로우 관계 {initialFollows.toLocaleString()}건</div>
        </div>
        <div className="lm-rep__social-arrow">→</div>
        <div className="lm-rep__social-side">
          <div className="lm-rep__social-tag">광장 종료 (R50)</div>
          <SocialMiniGraph stage="final" />
          <div className="lm-rep__social-stat">
            팔로우 관계 {finalFollows.toLocaleString()}건 (+{newFollows.toLocaleString()})
          </div>
        </div>
      </div>
      <div className="lm-rep__social-metrics">
        <Stat label="새 팔로우" value={newFollows.toLocaleString()} align="left" />
        <Stat label="클러스터" value={cliqueCount} align="left" />
        <Stat label="브릿지 에이전트" value={bridgeAgents} align="left" />
        <Stat label="리와이어 비율" value={`${(rewireRate * 100).toFixed(0)}%`} align="left" />
      </div>
    </div>
  );
}

function SocialMiniGraph({ stage }: { stage: 'initial' | 'final' }) {
  const W = 280,
    H = 200;
  const rng = lm.mulberry32(stage === 'initial' ? 11 : 17);
  const nodeCount = 28;
  const nodes: { x: number; y: number; role: string; isAnchor: boolean }[] = [];
  for (let i = 0; i < nodeCount; i++) {
    const role =
      i < 5 ? lm.ANCHORS[i].role : rng() < 0.5 ? 'citizen_p' : rng() < 0.5 ? 'citizen_m' : 'broadcast';
    const x =
      stage === 'initial'
        ? 0.1 + rng() * 0.8
        : Math.min(
            0.95,
            Math.max(0.05, (lm.ROLE_IDEOLOGY[role as keyof typeof lm.ROLE_IDEOLOGY] ?? 0) * 0.36 + 0.5 + (rng() - 0.5) * 0.18)
          );
    const y = 0.1 + rng() * 0.8;
    nodes.push({ x, y, role, isAnchor: i < 5 });
  }
  const edgeCount = stage === 'initial' ? 24 : 48;
  const edges: [number, number][] = [];
  for (let e = 0; e < edgeCount; e++) {
    const a = Math.floor(rng() * nodeCount);
    let b = Math.floor(rng() * nodeCount);
    if (a === b) b = (b + 1) % nodeCount;
    edges.push([a, b]);
  }
  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="lm-rep__social-svg">
      {edges.map(([a, b], i) => (
        <line
          key={i}
          x1={nodes[a].x * W}
          y1={nodes[a].y * H}
          x2={nodes[b].x * W}
          y2={nodes[b].y * H}
          stroke="#5C5447"
          strokeWidth="0.6"
          opacity="0.32"
        />
      ))}
      {nodes.map((n, i) => (
        <circle
          key={i}
          cx={n.x * W}
          cy={n.y * H}
          r={n.isAnchor ? 6 : 3.5}
          fill={lm.ROLE_BY_ID[n.role as keyof typeof lm.ROLE_BY_ID].color}
          opacity={n.isAnchor ? 1 : 0.78}
        />
      ))}
    </svg>
  );
}

// --------------------------------------------------------------------
// CostPanel
// --------------------------------------------------------------------
function CostPanel() {
  const c = REPORT_DATA.cost;
  return (
    <div className="lm-rep__cost-grid">
      <div className="lm-rep__cost-cell">
        <div className="lm-rep__cost-k">토큰 사용량</div>
        <div className="lm-rep__cost-v">{c.tokens.toLocaleString()}</div>
        <div className="lm-rep__cost-sub">in + out 누적</div>
      </div>
      <div className="lm-rep__cost-cell">
        <div className="lm-rep__cost-k">모델 호출 수</div>
        <div className="lm-rep__cost-v">{c.calls.toLocaleString()}</div>
        <div className="lm-rep__cost-sub">312 에이전트 × 50R</div>
      </div>
      <div className="lm-rep__cost-cell">
        <div className="lm-rep__cost-k">LLM 평균 지연</div>
        <div className="lm-rep__cost-v">
          {c.latencyAvg}
          <small>s</small>
        </div>
        <div className="lm-rep__cost-sub">p95 = 2.3s</div>
      </div>
      <div className="lm-rep__cost-cell">
        <div className="lm-rep__cost-k">fallback 비율</div>
        <div className="lm-rep__cost-v">
          {c.fallbackPct}
          <small>%</small>
        </div>
        <div className="lm-rep__cost-sub">로컬 모델 폴백</div>
      </div>
      <div className="lm-rep__cost-cell lm-rep__cost-cell--total">
        <div className="lm-rep__cost-k">총 비용</div>
        <div className="lm-rep__cost-v">₩ {c.krw.toLocaleString()}</div>
        <div className="lm-rep__cost-sub">Standard 플랜</div>
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// ScreenReport — 메인
// --------------------------------------------------------------------
export default function Report() {
  const { plazaId } = useParams<{ plazaId: string }>();
  const go = useScreenNav(plazaId);
  const nodes = useMemo(() => lm.generatePlaza({ seed: 42, n: 312 }), []);
  const pred = REPORT_DATA.prediction;
  const total = REPORT_DATA.total;
  const [backendReport, setBackendReport] = useState<PlazaReportResponse | null>(null);

  // /report fetch — composing 단계에서도 200 (markdown 만 비어있을 수 있음).
  // status='completed' 또는 markdown 도착 시 hero 가 진짜 본문으로 교체된다.
  useEffect(() => {
    if (!plazaId) return;
    let cancelled = false;
    api
      .getReport(plazaId)
      .then((res) => {
        if (!cancelled) setBackendReport(res);
      })
      .catch(() => {
        // mock 유지.
      });
    return () => {
      cancelled = true;
    };
  }, [plazaId]);

  const reportMarkdown = backendReport?.report_markdown ?? null;
  const reportFallbackUsed = backendReport?.report_fallback_used ?? false;
  const nAgents = backendReport?.n_agents ?? 312;
  const nRounds = backendReport?.n_rounds ?? total;

  // 다운로드 — markdown 은 Blob, PDF 는 브라우저 print (print dialog 에서 "PDF 로 저장")
  const baseName = `litemiro-report-${plazaId ?? 'demo'}`;
  const handleDownloadMarkdown = () => {
    if (!reportMarkdown) return;
    const blob = new Blob([reportMarkdown], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = `${baseName}.md`;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  };
  const handlePrintPdf = () => {
    window.print();
  };

  return (
    <div className="lm-rep">
      <div className="lm-rep__pad">
        {/* HEADER */}
        <header className="lm-rep__head">
          <div className="lm-rep__head-left">
            <div className="lm-rep__head-eyebrow">
              Phase 6 · 결과 리포트 · R{nRounds}/{nRounds}
            </div>
            <h1 className="lm-rep__head-title">주 4일제 도입 시뮬레이션 리포트</h1>
            <div className="lm-rep__head-meta">
              <span>광장 ID · {plazaId ?? lm.SEED.id}</span>
              <span>2026 · 05 · 23 · 18:42</span>
              <span>{nAgents}명 · {nRounds}라운드</span>
            </div>
          </div>
          <div className="lm-rep__head-actions">
            <Button kind="ghost" onClick={() => go('plaza')}>
              광장으로
            </Button>
            <Button kind="secondary" onClick={handleDownloadMarkdown} disabled={!reportMarkdown}>
              Markdown
            </Button>
            <Button kind="secondary" onClick={handlePrintPdf}>PDF</Button>
            <Button kind="primary" trailing={<ArrowGlyph dir="right" />}>
              공유
            </Button>
          </div>
        </header>

        {/* PREDICTION HERO — backend markdown 우선, 없으면 mock pred.headline */}
        <section className="lm-rep__hero">
          <div className="lm-rep__hero-tag">PREDICTION · 핵심 예측</div>
          {reportMarkdown ? (
            <div className="lm-rep__hero-markdown">
              <ReactMarkdown>{reportMarkdown}</ReactMarkdown>
            </div>
          ) : (
            <p className="lm-rep__hero-text">
              {reportFallbackUsed ? '보고서 본문 합성 폴백 — 통계만 확인 가능.' : pred.headline}
            </p>
          )}
          <div className="lm-rep__hero-meta">
            <span>
              신뢰도 · <b>medium-high</b>
            </span>
          </div>
        </section>

        {/* SECTIONS */}
        <ReportSection id="summary" num="01" title="전체 요약" sub="시뮬레이션 핵심 결과를 한눈에 정리합니다.">
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
                  <span>← 비판적</span>
                  <span>중립</span>
                  <span>우호적 →</span>
                </div>
              </div>
            </div>
            <div className="lm-rep__sum-side">
              <div className="lm-rep__sum-stats">
                <Stat label="참여 인격" value={312} />
                <Stat label="라운드" value={`${total}/${total}`} />
                <Stat label="총 발화" value={REPORT_DATA.counts.utterances.toLocaleString()} />
                <Stat label="총 인용" value={REPORT_DATA.counts.citations.toLocaleString()} />
                <Stat label="앵커 (이름)" value={REPORT_DATA.anchors.length} />
                <Stat label="대화 진영" value="3 (찬·중·반)" />
              </div>
              <div className="lm-rep__sum-bullets">
                <div className="lm-rep__sum-bullet">
                  <span className="lm-rep__sum-b-n">01</span>
                  <span className="lm-rep__sum-b-t">
                    미디어 앵커 3명이 <b>교차 인용의 62%</b>를 만들었어요.
                  </span>
                </div>
                <div className="lm-rep__sum-bullet">
                  <span className="lm-rep__sum-b-n">02</span>
                  <span className="lm-rep__sum-b-t">
                    시민 진영에서 <b>derived #47</b>이 영향력 상위 9위로 승격되었어요.
                  </span>
                </div>
                <div className="lm-rep__sum-bullet">
                  <span className="lm-rep__sum-b-n">03</span>
                  <span className="lm-rep__sum-b-t">중립 클러스터는 50 라운드 내내 의견을 유지했어요.</span>
                </div>
                <div className="lm-rep__sum-bullet">
                  <span className="lm-rep__sum-b-n">04</span>
                  <span className="lm-rep__sum-b-t">
                    진영을 가로지른 발화의 <b>78%</b>가 R30 이후에 집중되었어요.
                  </span>
                </div>
              </div>
            </div>
          </div>
        </ReportSection>

        <ReportSection
          id="behavior"
          num="02"
          title="행동 분석"
          sub="게시글·좋아요·리포스트·인용·팔로우 비율로 광장의 활동 패턴을 봅니다."
        >
          <ActionDistribution />
        </ReportSection>

        <ReportSection
          id="topic"
          num="03"
          title="토픽 분석"
          sub="어떤 주제가 많이 언급되고, 각 주제 안에서 진영이 어떻게 갈렸는지."
        >
          <TopicChart />
        </ReportSection>

        <ReportSection
          id="time"
          num="04"
          title="라운드별 변화"
          sub="시간에 따라 발화량과 진영 비율이 어떻게 변했는지. 변곡점은 점선으로 표시."
        >
          <div className="lm-rep__series-card">
            <RoundSeriesChart />
            <div className="lm-rep__series-legend">
              <span>
                <i className="lm-rep__series-line" />
                발화량 (좌측 축)
              </span>
              <span>
                <i className="lm-rep__series-dot" style={{ background: '#4F7591' }} />
                비판적
              </span>
              <span>
                <i className="lm-rep__series-dot" style={{ background: '#8B8170' }} />
                중립
              </span>
              <span>
                <i className="lm-rep__series-dot" style={{ background: '#C77B4F' }} />
                우호적
              </span>
            </div>
          </div>
        </ReportSection>

        <ReportSection
          id="influence"
          num="05"
          title="영향력 분석"
          sub="어떤 에이전트가 확산을 많이 만들었는지. 화제성·교차 인용·전파·팔로워 변화 종합."
        >
          <InfluenceTable />
        </ReportSection>

        <ReportSection
          id="social"
          num="06"
          title="소셜 그래프 분석"
          sub="팔로우와 리포스트 관계가 광장이 진행되며 어떻게 변했는지."
        >
          <SocialDelta />
        </ReportSection>

        <ReportSection
          id="cost"
          num="07"
          title="비용 분석"
          sub="토큰 사용량, 모델 호출 수, LLM 지연, fallback 비율과 예상 비용."
        >
          <CostPanel />
        </ReportSection>

        <ReportSection id="conclusion" num="08" title="결론" sub="시뮬레이션 결과를 어떻게 해석할지.">
          <div className="lm-rep__conc">
            <div className="lm-rep__conc-headline">
              <span className="lm-rep__conc-tag">예측</span>
              <p>{pred.headline}</p>
            </div>
            <div className="lm-rep__conc-bullets">
              {pred.bullets.map((b, i) => (
                <div key={i} className={`lm-rep__conc-bullet lm-rep__conc-bullet--${b.kind}`}>
                  <span className="lm-rep__conc-kind">
                    {b.kind === 'pro' ? '추진 신호' : b.kind === 'con' ? '저항 신호' : '참고'}
                  </span>
                  <p>{b.text}</p>
                </div>
              ))}
            </div>
            <div className="lm-rep__conc-reco">
              <div className="lm-rep__conc-reco-head">후속 권장 액션</div>
              <ul>
                {pred.recommendations.map((r, i) => (
                  <li key={i}>{r}</li>
                ))}
              </ul>
            </div>
          </div>
        </ReportSection>

        {/* FOOTER */}
        <footer className="lm-rep__foot">
          <div className="lm-rep__foot-meta">
            <span>LiteMiro v3.4 · 생성 시각 2026-05-23 18:42 KST</span>
          </div>
          <div className="lm-rep__foot-actions">
            <Button kind="secondary">새 시드로 다시 시뮬레이션</Button>
            <Button kind="primary" trailing={<ArrowGlyph dir="right" />}>
              이 리포트 저장
            </Button>
          </div>
        </footer>
      </div>
    </div>
  );
}
