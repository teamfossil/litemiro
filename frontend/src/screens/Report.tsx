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
import type { PlazaNode } from '@/data/types';
import { Button, Stat, ArrowGlyph } from '@/components/atoms';
import { useScreenNav } from '@/lib/nav';
import { api, type PlazaLayoutResponse, type PlazaReportResponse } from '@/api/client';
import { mapBackendRoleToRoleId } from '@/lib/roles';

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
// ActionDistribution — 행동 분포. ratios + total 은 백엔드 응답을 그대로 받음.
// --------------------------------------------------------------------
const ACTION_META: Record<string, { label: string; tone: string }> = {
  CREATE_POST: { label: '발언', tone: 'create' },
  LIKE_POST: { label: '호응', tone: 'like' },
  REPOST: { label: '전파', tone: 'repost' },
  QUOTE_POST: { label: '인용', tone: 'quote' },
  FOLLOW: { label: '팔로우', tone: 'follow' },
  DO_NOTHING: { label: '관망', tone: 'idle' },
};

function ActionDistribution({ ratios, total, rounds }: { ratios: Record<string, number>; total: number; rounds: number }) {
  const rows = Object.entries(ratios)
    .filter(([type]) => ACTION_META[type])
    .sort((a, b) => b[1] - a[1]);

  if (total === 0) {
    return <div className="lm-rep__empty">행동이 기록되지 않았습니다.</div>;
  }

  return (
    <div className="lm-rep__actions">
      {rows.map(([type, pct]) => {
        const meta = ACTION_META[type];
        const count = Math.round(total * pct);
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
        총 행동 {total.toLocaleString()}건{rounds > 0 ? ` · 라운드당 평균 ${(total / rounds).toFixed(1)}건` : ''}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// RoundSeriesChart — 라운드별 행동 수 + 활성 에이전트 (백엔드 time_series).
// 백엔드는 라운드별 n_actions / n_active_agents / do_nothing_ratio 만 줌 —
// 진영 비율은 측정값이 없어서 라인 두 개 (활동 / 활성) 로만 표시.
// --------------------------------------------------------------------
interface SeriesPoint {
  round_num: number;
  n_actions: number;
  n_active_agents: number;
  do_nothing_ratio: number;
}

function RoundSeriesChart({ data }: { data: SeriesPoint[] }) {
  const W = 1400, H = 360;
  const pad = { l: 56, r: 24, t: 24, b: 40 };
  const w = W - pad.l - pad.r;
  const h = H - pad.t - pad.b;
  const total = data.length;

  if (total === 0) {
    return <div className="lm-rep__empty">라운드별 활동이 기록되지 않았습니다.</div>;
  }

  const maxActions = Math.max(...data.map((d) => d.n_actions), 1);
  const maxActive = Math.max(...data.map((d) => d.n_active_agents), 1);
  const x = (i: number) => pad.l + (total > 1 ? (i / (total - 1)) * w : w / 2);

  const linePath = (key: 'n_actions' | 'n_active_agents', max: number) =>
    data.map((d, i) => {
      const xi = x(i);
      const yi = pad.t + (1 - d[key] / max) * h;
      return `${i === 0 ? 'M' : 'L'} ${xi} ${yi}`;
    }).join(' ');

  const ticks = total <= 10 ? data.map((d) => d.round_num) : [1, 5, 10, 20, 30, 40, 50].filter((r) => r <= data[data.length - 1].round_num);

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="lm-rep__series-svg" role="img" aria-label="라운드별 활동 및 활성 에이전트">
      <path d={linePath('n_actions', maxActions)} fill="none" stroke="#1A1813" strokeWidth="1.6" strokeLinecap="round" />
      <path d={linePath('n_active_agents', maxActive)} fill="none" stroke="#4F7591" strokeWidth="1.4" strokeDasharray="4 4" strokeLinecap="round" />

      {ticks.map((r) => {
        const i = data.findIndex((d) => d.round_num === r);
        if (i < 0) return null;
        return (
          <text key={r} x={x(i)} y={H - 14} fontFamily="IBM Plex Mono" fontSize="12" fill="#8E8674" textAnchor="middle">
            R{r}
          </text>
        );
      })}

      <text x={20} y={pad.t + 12} fontFamily="IBM Plex Mono" fontSize="12" fill="#8E8674">활동수</text>
      <text x={20} y={H - pad.b - 4} fontFamily="IBM Plex Mono" fontSize="12" fill="#8E8674">0</text>
    </svg>
  );
}

// --------------------------------------------------------------------
// CostPanel — 백엔드 tokens_used + qa_metrics 사용.
// 호출수/지연/요금은 백엔드가 안 줘서 표시 안 함.
// --------------------------------------------------------------------
function CostPanel({ tokens, qa, nAgents, nRounds }: { tokens: number; qa: Record<string, number>; nAgents: number; nRounds: number }) {
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
// ScreenReport — 메인
// --------------------------------------------------------------------
export default function Report() {
  const { plazaId } = useParams<{ plazaId: string }>();
  const go = useScreenNav(plazaId);
  const [backendReport, setBackendReport] = useState<PlazaReportResponse | null>(null);
  const [layout, setLayout] = useState<PlazaLayoutResponse | null>(null);

  useEffect(() => {
    if (!plazaId) return;
    let cancelled = false;
    api.getReport(plazaId).then((res) => { if (!cancelled) setBackendReport(res); }).catch(() => {});
    api.getLayout(plazaId).then((res) => { if (!cancelled) setLayout(res); }).catch(() => {});
    return () => { cancelled = true; };
  }, [plazaId]);

  // /layout 의 agents 를 MiniPlaza 가 먹는 PlazaNode 형태로 변환. ready=false 면 빈 배열.
  const nodes = useMemo<PlazaNode[]>(() => {
    if (!layout || !layout.ready) return [];
    return layout.agents.map((a) => {
      const roleId = mapBackendRoleToRoleId(a.role);
      return {
        id: a.id,
        name: a.name,
        role: roleId,
        kind: 'anchor',
        x: a.x,
        y: a.y,
        influence: a.influence,
        color: lm.ROLE_BY_ID[roleId].color,
        anchor: a.influence > 0.6,
      };
    });
  }, [layout]);

  const reportMarkdown = backendReport?.report_markdown ?? null;
  const reportFallbackUsed = backendReport?.report_fallback_used ?? false;
  const nAgents = backendReport?.n_agents ?? 0;
  const nRounds = backendReport?.n_rounds ?? 0;
  const nEvents = backendReport?.n_events ?? 0;
  const tokensUsed = backendReport?.tokens_used ?? 0;
  const roundsDone = backendReport?.rounds_done ?? 0;
  const roundsTotal = backendReport?.rounds_total ?? 0;
  const reportStatus = backendReport?.status ?? null;

  // 백엔드 categories 에서 안전하게 뽑기. shape 가 자유 dict 라 타입 단언 후 가드.
  const cats = (backendReport?.categories ?? {}) as Record<string, Record<string, unknown>>;
  const actionDist = cats.action_distribution ?? {};
  const actionRatios = (actionDist.ratios as Record<string, number>) ?? {};
  const actionTotal = (actionDist.total as number) ?? 0;
  const timeSeries = cats.time_series ?? {};
  const seriesData = ((timeSeries.series as SeriesPoint[]) ?? []) as SeriesPoint[];
  const networkMetrics = (cats.network_metrics as { n_follow_events?: number; top_followed?: { agent_id: string; followers: number }[]; top_followers?: { agent_id: string; following: number }[] }) ?? {};
  const topicFlow = (cats.topic_flow as { n_posts?: number; top_posters?: { agent_id: string; posts: number }[]; samples?: { round_num: number; agent_id: string; content: string }[] }) ?? {};
  const qaMetrics = (backendReport?.qa_metrics ?? {}) as Record<string, number>;
  const hasBackend = !!backendReport;

  // 다운로드 — markdown 은 Blob, PDF 는 브라우저 print (print dialog 에서 "PDF 로 저장")
  const baseName = `litemiro-report-${plazaId ?? 'unknown'}`;
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
              Phase 6 · 결과 리포트 · R{roundsDone}/{roundsTotal}
            </div>
            <h1 className="lm-rep__head-title">시뮬레이션 결과 리포트</h1>
            <div className="lm-rep__head-meta">
              {backendReport?.label && <span>{backendReport.label}</span>}
              <span>광장 ID · {plazaId ?? '—'}</span>
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
          </div>
        </header>

        {/* PREDICTION HERO — backend markdown 우선, 미수신 시 placeholder */}
        <section className="lm-rep__hero">
          <div className="lm-rep__hero-tag">PREDICTION · 핵심 예측</div>
          {reportMarkdown ? (
            <div className="lm-rep__hero-markdown">
              <ReactMarkdown>{reportMarkdown}</ReactMarkdown>
            </div>
          ) : (
            <p className="lm-rep__hero-text">
              {reportFallbackUsed ? '보고서 본문 합성 폴백 — 통계만 확인 가능합니다.' : '시뮬레이션 결과 보고서를 불러오는 중입니다.'}
            </p>
          )}
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
                {nodes.length > 0 ? (
                  <>
                    <MiniPlaza nodes={nodes} />
                    <div className="lm-rep__sum-plaza-axis">
                      <span>← 비판적</span>
                      <span>중립</span>
                      <span>우호적 →</span>
                    </div>
                  </>
                ) : (
                  <div className="lm-rep__empty">광장 좌표 데이터를 불러오는 중입니다.</div>
                )}
              </div>
            </div>
            <div className="lm-rep__sum-side">
              <div className="lm-rep__sum-stats">
                <Stat label="참여 인격" value={nAgents} />
                <Stat label="라운드" value={`${roundsDone}/${roundsTotal}`} />
                <Stat label="총 이벤트" value={nEvents.toLocaleString()} />
                <Stat label="총 행동" value={actionTotal.toLocaleString()} />
                <Stat label="게시물" value={(topicFlow.n_posts ?? 0).toLocaleString()} />
                <Stat label="팔로우 이벤트" value={(networkMetrics.n_follow_events ?? 0).toLocaleString()} />
              </div>
              {!hasBackend && (
                <div className="lm-rep__empty">백엔드 보고서를 불러오는 중입니다.</div>
              )}
            </div>
          </div>
        </ReportSection>

        <ReportSection
          id="behavior"
          num="02"
          title="행동 분석"
          sub="게시글·좋아요·리포스트·인용·팔로우 비율로 광장의 활동 패턴을 봅니다."
        >
          <ActionDistribution ratios={actionRatios} total={actionTotal} rounds={nRounds} />
        </ReportSection>

        <ReportSection
          id="topic"
          num="03"
          title="토픽 분석"
          sub="라운드별 게시물 흐름과 가장 활발하게 발언한 에이전트."
        >
          {(topicFlow.n_posts ?? 0) === 0 ? (
            <div className="lm-rep__empty">게시물이 생성되지 않아 토픽 분석을 수행할 수 없습니다.</div>
          ) : (
            <div className="lm-rep__topics">
              <div className="lm-rep__topic-row">
                <span className="lm-rep__topic-name">총 게시물</span>
                <span className="lm-rep__topic-count">{topicFlow.n_posts}</span>
              </div>
              {(topicFlow.top_posters ?? []).slice(0, 5).map((p) => (
                <div key={p.agent_id} className="lm-rep__topic-row">
                  <span className="lm-rep__topic-name">{p.agent_id}</span>
                  <span className="lm-rep__topic-count">{p.posts} 건</span>
                </div>
              ))}
              {(topicFlow.samples ?? []).slice(0, 3).map((s, i) => (
                <div key={i} className="lm-rep__topic-sample">
                  <div className="lm-rep__topic-sample-head">R{s.round_num} · {s.agent_id}</div>
                  <div className="lm-rep__topic-sample-body">{s.content}</div>
                </div>
              ))}
            </div>
          )}
        </ReportSection>

        <ReportSection
          id="time"
          num="04"
          title="라운드별 변화"
          sub="시간에 따라 활동 수와 활성 에이전트가 어떻게 변했는지."
        >
          <div className="lm-rep__series-card">
            <RoundSeriesChart data={seriesData} />
            <div className="lm-rep__series-legend">
              <span><i className="lm-rep__series-line" /> 활동 수</span>
              <span><i className="lm-rep__series-line" style={{ borderTopStyle: 'dashed' }} /> 활성 에이전트</span>
            </div>
          </div>
        </ReportSection>

        <ReportSection
          id="influence"
          num="05"
          title="영향력 분석"
          sub="팔로우를 가장 많이 받은 에이전트 / 가장 많이 팔로우한 에이전트."
        >
          {((networkMetrics.top_followed ?? []).length === 0 && (networkMetrics.top_followers ?? []).length === 0) ? (
            <div className="lm-rep__empty">팔로우 활동이 없어 영향력 순위를 계산할 수 없습니다.</div>
          ) : (
            <div className="lm-rep__inf">
              <header className="lm-rep__inf-head">
                <span className="lm-rep__inf-col-rank">순위</span>
                <span className="lm-rep__inf-col-who">에이전트</span>
                <span className="lm-rep__inf-col-fol">팔로워 +</span>
              </header>
              {(networkMetrics.top_followed ?? []).slice(0, 10).map((row, i) => (
                <div key={row.agent_id} className="lm-rep__inf-row">
                  <span className="lm-rep__inf-col-rank">{String(i + 1).padStart(2, '0')}</span>
                  <span className="lm-rep__inf-col-who">{row.agent_id}</span>
                  <span className="lm-rep__inf-col-fol">+{row.followers}</span>
                </div>
              ))}
            </div>
          )}
        </ReportSection>

        <ReportSection
          id="social"
          num="06"
          title="소셜 그래프 분석"
          sub="광장에서 발생한 팔로우 관계 요약."
        >
          {(networkMetrics.n_follow_events ?? 0) === 0 ? (
            <div className="lm-rep__empty">팔로우 이벤트가 발생하지 않았습니다.</div>
          ) : (
            <div className="lm-rep__social-metrics">
              <Stat label="총 팔로우 이벤트" value={(networkMetrics.n_follow_events ?? 0).toLocaleString()} align="left" />
              <Stat label="피팔로우 상위" value={(networkMetrics.top_followed ?? []).length} align="left" />
              <Stat label="팔로우 상위" value={(networkMetrics.top_followers ?? []).length} align="left" />
            </div>
          )}
        </ReportSection>

        <ReportSection
          id="cost"
          num="07"
          title="비용 · 품질 지표"
          sub="토큰 사용량과 시뮬레이션 품질 메트릭."
        >
          <CostPanel tokens={tokensUsed} qa={qaMetrics} nAgents={nAgents} nRounds={nRounds} />
        </ReportSection>

        {/* FOOTER — 백엔드가 주는 status / fallback 표시만 */}
        {reportStatus && (
          <footer className="lm-rep__foot">
            <div className="lm-rep__foot-meta">
              <span>광장 상태 · {reportStatus}</span>
              {reportFallbackUsed && <span>· 본문 합성 폴백 사용</span>}
            </div>
          </footer>
        )}
      </div>
    </div>
  );
}
