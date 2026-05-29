// =====================================================================
// 캐스팅 (Phase 4) — Persona Generation Gate
//
// 두 단계 (라우트 분리):
//
//   /casting/new?ontology=...&preset=...&rounds=...&label=...
//     → CastingLoading. ontology 폴링 + 단순 로딩 표시. ready 시
//        /api/plazas 만들어 /casting/{plaza_id} 로 replace.
//
//   /casting/:plazaId
//     → CastingReveal. /api/plazas/{id}/agents 로 생성된 인격 리스트를
//        받아 카드 그리드로 보여준다. 사용자가 "광장으로 입장" 을 눌러
//        /live/{plaza_id} 로 이동.
//
// 백엔드는 ontology generation 중 entity/profile/memory 단계의 부분
// 데이터를 노출하지 않으므로 (status / agent_count 만) Phase 1 시각화는
// 단순 spinner 로 한정. 의미 있는 데이터는 완료 후 /agents 가 한꺼번에 줌.
// =====================================================================

import { useEffect, useState } from 'react';
import { useLocation, useNavigate, useParams, useSearchParams } from 'react-router-dom';
import { lm } from '@/data/mock';
import { Button, ArrowGlyph } from '@/components/atoms';
import { api, ApiError, type OntologyResponse, type PlazaAgentItem, type Preset } from '@/api/client';
import { mapBackendRoleToRoleId } from '@/lib/roles';

const ONTOLOGY_POLL_INTERVAL_MS = 2_000;
const DEFAULT_ROUNDS = 15;

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

// --------------------------------------------------------------------
// CastingLoading — ontology 폴링 단계.
// --------------------------------------------------------------------
type LoadingPhase = 'polling' | 'launching' | 'failed';

function CastingLoading() {
  const [search] = useSearchParams();
  const navigate = useNavigate();
  const ontologyId = search.get('ontology') ?? '';
  const labelParam = search.get('label') ?? '';
  const presetParam = search.get('preset') ?? 'standard';
  const roundsParam = Number(search.get('rounds') ?? DEFAULT_ROUNDS);

  const preset: Preset = isPreset(presetParam) ? presetParam : 'standard';
  const rounds =
    Number.isFinite(roundsParam) && roundsParam > 0 ? Math.floor(roundsParam) : DEFAULT_ROUNDS;
  const targetCount = preset === 'quick' ? 100 : preset === 'full' ? 500 : 300;

  const [status, setStatus] = useState<OntologyResponse | null>(null);
  const [phase, setPhase] = useState<LoadingPhase>('polling');
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
    const ac = new AbortController();
    const startedAt = Date.now();
    const elapsedTimer = window.setInterval(() => {
      if (!cancelled) setElapsedSec(Math.floor((Date.now() - startedAt) / 1000));
    }, 1000);

    const tick = async () => {
      if (cancelled) return;
      let onto: OntologyResponse;
      try {
        onto = await api.getOntology(ontologyId, ac.signal);
      } catch (e) {
        // abort 는 화면 이탈에 따른 정상 취소 — 에러 상태로 만들지 않는다.
        if (cancelled || ac.signal.aborted) return;
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
            // ready 후 곧장 Live 가 아니라 CastingReveal 로 — 사용자에 추출된
            // 인격을 한 번 보여준 뒤 "광장으로 입장" 버튼을 눌러야 Live 진입.
            navigate(`/casting/${encodeURIComponent(plaza.plaza_id)}`, { replace: true });
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
      ac.abort();
      if (pollTimer) clearTimeout(pollTimer);
      window.clearInterval(elapsedTimer);
    };
  }, [ontologyId, rounds, preset, labelParam, navigate]);

  // preset 별 기대 소요. quick 1분, standard 2분, full 8분 정도가 정상 분포 —
  // 1.5× 넘으면 사용자에 "정상이니 그대로 두세요" 신호를 명시. 분 단위 dead-screen
  // 에서 사용자가 "탭 닫고 나갈까" 고민할 정공.
  const expectedSec = preset === 'quick' ? 60 : preset === 'full' ? 480 : 120;
  const isOverdue = phase === 'polling' && elapsedSec > expectedSec * 1.5;

  const headTitle =
    phase === 'failed'
      ? '문제가 발생했어요'
      : phase === 'launching'
        ? '광장을 여는 중…'
        : `${targetCount}명 인격을 만들고 있어요`;
  const subText =
    phase === 'failed'
      ? (error ?? '알 수 없는 오류')
      : phase === 'launching'
        ? '곧 자동으로 다음 단계로 넘어갑니다.'
        : isOverdue
          ? `평소보다 조금 더 걸리고 있어요 · ${formatElapsed(elapsedSec)} 경과. 그대로 두면 곧 완료돼요.`
          : `LLM 호출이 진행되고 있어요 · ${formatElapsed(elapsedSec)} 경과`;

  return (
    <div className="lm-cast">
      <div className="lm-cast__pad">
        <header className="lm-cast__head">
          <div className="lm-cast__head-left">
            <div className="lm-cast__head-eyebrow">Phase 4 · 인격 생성</div>
            <h1 className="lm-cast__head-title">{headTitle}</h1>
            <div className="lm-cast__head-status">
              <span className="lm-cast__head-status-tag">{phase === 'failed' ? 'failed' : (status?.status ?? 'pending')}</span>
              <span className="lm-cast__head-status-text">{subText}</span>
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

        <div className="lm-cast__loading">
          {phase === 'failed' ? (
            <p className="lm-cast__loading-error">{error ?? '알 수 없는 오류'}</p>
          ) : (
            <>
              <div className="lm-cast__loading-spinner" aria-hidden="true" />
              <p className="lm-cast__loading-hint">
                자료에서 핵심 인물·기관을 뽑고 {targetCount}명 시민 인격을 빚는 중입니다.
                보통 분 단위가 걸려요. 이 화면에 머물러 있으면 자동으로 다음 단계로 넘어갑니다.
              </p>
              {status?.agent_count != null && (
                <p className="lm-cast__loading-progress">
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
// CastingReveal — ontology 완료 후 추출된 인격 카드 그리드.
// --------------------------------------------------------------------
function CastingReveal() {
  const { plazaId } = useParams<{ plazaId: string }>();
  const navigate = useNavigate();
  const [agents, setAgents] = useState<PlazaAgentItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!plazaId) return;
    const ac = new AbortController();
    api
      .getAgents(plazaId, ac.signal)
      .then((res) => {
        setAgents(res.agents);
      })
      .catch((e) => {
        // abort 는 화면 이탈에 따른 정상 취소 — 에러 메시지를 띄우지 않는다.
        if (ac.signal.aborted) return;
        setError(formatError(e, '인격 목록 조회 실패'));
      });
    return () => {
      ac.abort();
    };
  }, [plazaId]);

  const handleEnterLive = () => {
    if (!plazaId) return;
    navigate(`/live/${encodeURIComponent(plazaId)}`);
  };

  if (error) {
    return (
      <div className="lm-cast">
        <div className="lm-cast__pad">
          <header className="lm-cast__head">
            <div className="lm-cast__head-left">
              <div className="lm-cast__head-eyebrow">Phase 4 · 인격 추출</div>
              <h1 className="lm-cast__head-title">문제가 발생했어요</h1>
              <div className="lm-cast__head-status">
                <span className="lm-cast__head-status-text">{error}</span>
              </div>
            </div>
            <div className="lm-cast__head-actions">
              <Button kind="primary" onClick={() => navigate('/seed', { replace: true })}>
                시드로 돌아가기
              </Button>
            </div>
          </header>
        </div>
      </div>
    );
  }

  if (agents === null) {
    return (
      <div className="lm-cast">
        <div className="lm-cast__pad">
          <header className="lm-cast__head">
            <div className="lm-cast__head-left">
              <div className="lm-cast__head-eyebrow">Phase 4 · 인격 추출</div>
              <h1 className="lm-cast__head-title">인격 목록을 불러오는 중…</h1>
            </div>
          </header>
          <div className="lm-cast__loading">
            <div className="lm-cast__loading-spinner" aria-hidden="true" />
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="lm-cast">
      <div className="lm-cast__pad">
        <header className="lm-cast__head">
          <div className="lm-cast__head-left">
            <div className="lm-cast__head-eyebrow">Phase 4 · 인격 추출 완료</div>
            <h1 className="lm-cast__head-title">{agents.length}명의 인격이 모였습니다</h1>
            <div className="lm-cast__head-status">
              <span className="lm-cast__head-status-text">
                각 카드에 이름·역할·성향·관심 주제가 표시돼요. 준비됐다면 광장으로 입장하세요.
              </span>
            </div>
          </div>
          <div className="lm-cast__head-actions">
            <Button kind="primary" onClick={handleEnterLive} trailing={<ArrowGlyph dir="right" />}>
              광장으로 입장
            </Button>
          </div>
        </header>

        <div className="lm-cast__rows">
          {agents.map((a) => (
            <AgentRow key={a.id} agent={a} />
          ))}
        </div>
      </div>
    </div>
  );
}

function AgentRow({ agent }: { agent: PlazaAgentItem }) {
  const roleId = mapBackendRoleToRoleId(agent.role);
  const role = lm.ROLE_BY_ID[roleId];
  const topics = agent.topics.slice(0, 3);
  return (
    <div className="lm-cast__row" style={{ borderLeftColor: role.color }}>
      <div className="lm-cast__row-name">
        <span className="lm-cast__row-name-role" style={{ color: role.color }}>
          <span className="lm-cast__row-name-role-dot" style={{ background: role.color }} />
          {role.name}
        </span>
        <span className="lm-cast__row-name-text">{agent.name}</span>
      </div>
      <div className="lm-cast__row-bar" aria-label={`ideology ${agent.ideology.toFixed(2)}`}>
        <span className="lm-cast__row-bar-label lm-cast__row-bar-label--left">비판적</span>
        <div className="lm-cast__row-bar-track">
          <span
            className="lm-cast__row-bar-tick"
            style={{ left: `${agent.ideology * 100}%`, background: role.color }}
          />
        </div>
        <span className="lm-cast__row-bar-label lm-cast__row-bar-label--right">우호적</span>
      </div>
      <div className="lm-cast__row-topics">
        {topics.map((t) => (
          <span key={t} className="lm-cast__row-topic">
            {t}
          </span>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// Casting (default) — URL 분기. /casting/new 만 polling 화면, 나머지는 reveal.
// --------------------------------------------------------------------
export default function Casting() {
  const location = useLocation();
  if (location.pathname === '/casting/new') return <CastingLoading />;
  return <CastingReveal />;
}
