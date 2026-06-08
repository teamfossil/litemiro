// =====================================================================
// 데모 캐스팅 — production CastingLoading + CastingReveal 흐름을 mock 으로
// 재현. API 호출 없이 ~300명 mock agents 를 row 카드 그리드로 보여준다.
// 데모 모드: 4초 로딩 → reveal 자동 전환.
// =====================================================================

import { useEffect, useMemo, useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { lm } from '@/data/mock';
import type { RoleId } from '@/data/types';
import { stanceColor } from '@/lib/plazaNodes';
import { Button, ArrowGlyph } from '@/components/atoms';

const LOADING_MS = 4000;
const TARGET_COUNT = 300;

// --------------------------------------------------------------------
// 한국어 이름 풀 — 시드 RNG 로 조합해 다양하게
// --------------------------------------------------------------------
const SURNAMES = ['김', '이', '박', '최', '정', '강', '조', '윤', '장', '임', '한', '오', '서', '신', '권', '황', '안', '송', '류', '전', '홍', '고', '문', '양', '손', '배', '백', '허', '유', '남'];
const GIVEN_NAMES = ['지훈', '민준', '서연', '지영', '예린', '준호', '수빈', '민서', '태양', '하은', '재원', '다은', '성민', '유진', '동현', '나영', '현우', '지수', '승현', '미래', '경훈', '소은', '진우', '혜린', '태준', '아름', '영민', '채원', '병철', '수현', '기현', '보람', '상훈', '지혜', '원준', '가영', '도현', '은지', '민혁', '세영', '찬호', '희진', '정훈', '예지', '건우', '나현', '재훈', '수정', '용준', '미진'];

// --------------------------------------------------------------------
// 역할별 topics 풀
// --------------------------------------------------------------------
const ROLE_TOPICS: Record<RoleId, string[]> = {
  broadcast:   ['노동 정책', '경제 보도', '산업 트랜지션', '현장 취재', '기업 인터뷰'],
  investig:    ['탐사 보도', '노동 착취', '규제 공백', '내부 고발', '데이터 저널리즘'],
  columnist:   ['사회 칼럼', '주4일제', '돌봄·교육', '노동 철학', '사회학'],
  politician:  ['근로기준법', '국회 발의', '정책 설계', '복지 예산', '입법 전략'],
  pundit:      ['노동 시장', '정책 비평', '여론 분석', '미디어 비평', '사회 갈등'],
  citizen_m:   ['일·생활 균형', '직장 문화', '육아 부담', '중소기업', '지역 경제'],
  citizen_p:   ['보육 지원', '시급제 보호', '돌봄 노동', '사회 불평등', '시민 권리'],
  citizen_x:   ['스타트업', '유연 근무', '디지털 전환', '자영업', '플랫폼 노동'],
  academic:    ['노동경제학', 'OECD 비교', '산업별 설계', '정책 평가', '복지 국가'],
  lawyer:      ['근로 계약', '법령 해석', '판례 분석', '노사 분쟁', '행정법'],
  ngo:         ['시범사업', '시민 연대', '캠페인', '공익 소송', '정책 로비'],
  corp:        ['기업 경쟁력', '비용 분담', '노사 협약', '인력 운용', '산업계 입장'],
};

// --------------------------------------------------------------------
// Mock agent 타입
// --------------------------------------------------------------------
interface MockAgent {
  id: string;
  name: string;
  role: RoleId;
  isAnchor: boolean;
  stance: number;
  topics: string[];
}

// --------------------------------------------------------------------
// generateMockAgents — 앵커 5명 + 파생 295명 (seed 42, 일관된 RNG)
// --------------------------------------------------------------------
function generateMockAgents(): MockAgent[] {
  const agents: MockAgent[] = [];

  // 1) 앵커 5명 — 이름/역할/stance 고정
  const ANCHOR_DATA: { id: string; name: string; role: RoleId; stance: number; topics: string[] }[] = [
    { id: 'cm', name: '최영민 기자',    role: 'broadcast',  stance: 0.22, topics: ['노동 정책', '산업 트랜지션', '경제 보도'] },
    { id: 'hj', name: '한지영 칼럼니스트', role: 'columnist',  stance: 0.74, topics: ['돌봄·교육', '주4일제', '사회학'] },
    { id: 'js', name: '정세훈 의원',    role: 'politician', stance: 0.30, topics: ['근로기준법', '국회 발의', '정책'] },
    { id: 'pk', name: '박서경 교수',    role: 'academic',   stance: 0.52, topics: ['노동경제학', 'OECD 비교', '산업별 설계'] },
    { id: 'no', name: '전국노동연대',   role: 'ngo',        stance: 0.78, topics: ['시범사업 확대', '시민 연대', '산업별 정책'] },
  ];
  for (const a of ANCHOR_DATA) {
    agents.push({ ...a, isAnchor: true });
  }

  // 2) 파생 에이전트 295명 — seeded RNG
  const rng = lm.mulberry32(42);
  const totalW = Object.values(lm.ROLE_COUNT_WEIGHT).reduce((a, b) => a + b, 0);
  const nDerived = TARGET_COUNT - ANCHOR_DATA.length;

  for (let i = 0; i < nDerived; i++) {
    // 역할 가중치 선택
    let pick = rng() * totalW;
    let roleId: RoleId = 'citizen_m';
    for (const [id, w] of Object.entries(lm.ROLE_COUNT_WEIGHT)) {
      pick -= w;
      if (pick <= 0) { roleId = id as RoleId; break; }
    }

    // 이름: 성 + 이름 RNG 조합
    const surname   = SURNAMES[Math.floor(rng() * SURNAMES.length)];
    const givenName = GIVEN_NAMES[Math.floor(rng() * GIVEN_NAMES.length)];
    const name = `${surname}${givenName}`;

    // stance: ideology 에 느슨히 연동 + 큰 노이즈 → 비판/중립/우호 골고루
    const ideo = lm.ROLE_IDEOLOGY[roleId] ?? 0;
    const ideoPull = ideo * 0.34;
    const noise = (rng() - 0.5) * 0.52; // 넓게 퍼지게
    const stance = lm.clamp(0.5 + ideoPull + noise, 0.02, 0.98);

    // topics: 역할 풀에서 2~3개 랜덤 선택
    const pool = ROLE_TOPICS[roleId] ?? [];
    const nTopics = 2 + Math.floor(rng() * 2); // 2 or 3
    const shuffled = [...pool].sort(() => rng() - 0.5);
    const topics = shuffled.slice(0, Math.min(nTopics, pool.length));

    agents.push({
      id: `d${i}`,
      name,
      role: roleId,
      isAnchor: false,
      stance,
      topics,
    });
  }

  return agents;
}

// --------------------------------------------------------------------
// AgentRow — production Casting 의 AgentRow 와 동일 셰이프
// --------------------------------------------------------------------
function AgentRow({ agent }: { agent: MockAgent }) {
  const role = lm.ROLE_BY_ID[agent.role];
  const tickColor = stanceColor(agent.stance);
  return (
    <div className="lm-cast__row" style={{ borderLeftColor: tickColor }}>
      <div className="lm-cast__row-name">
        <span className="lm-cast__row-name-role" style={{ color: role.color }}>
          <span className="lm-cast__row-name-role-dot" style={{ background: role.color }} />
          {role.name}
        </span>
        <span className="lm-cast__row-name-text">{agent.name}</span>
      </div>
      <div className="lm-cast__row-bar" aria-label={`stance ${agent.stance.toFixed(2)}`}>
        <span className="lm-cast__row-bar-label lm-cast__row-bar-label--left">비판적</span>
        <div className="lm-cast__row-bar-track">
          <span
            className="lm-cast__row-bar-tick"
            style={{ left: `${agent.stance * 100}%`, background: tickColor }}
          />
        </div>
        <span className="lm-cast__row-bar-label lm-cast__row-bar-label--right">우호적</span>
      </div>
      <div className="lm-cast__row-topics">
        {agent.topics.map((t) => (
          <span key={t} className="lm-cast__row-topic">{t}</span>
        ))}
      </div>
    </div>
  );
}

// --------------------------------------------------------------------
// CastingDemoMock — 로딩 → reveal
// --------------------------------------------------------------------
export default function CastingDemoMock() {
  const navigate = useNavigate();
  const [phase, setPhase] = useState<'loading' | 'reveal'>('loading');
  const [agentCount, setAgentCount] = useState(0);
  const [elapsedSec, setElapsedSec] = useState(0);

  // 300명 에이전트 — 한 번만 생성
  const agents = useMemo(() => generateMockAgents(), []);

  useEffect(() => {
    const startedAt = performance.now();
    let raf = 0;
    const tick = () => {
      const elapsed = performance.now() - startedAt;
      const t = Math.min(elapsed / LOADING_MS, 1);
      setAgentCount(Math.round(t * TARGET_COUNT));
      setElapsedSec(Math.floor(elapsed / 1000));
      if (t < 1) {
        raf = requestAnimationFrame(tick);
      } else {
        setPhase('reveal');
      }
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, []);

  if (phase === 'loading') {
    return (
      <div className="lm-cast">
        <div className="lm-cast__pad">
          <header className="lm-cast__head">
            <div className="lm-cast__head-left">
              <div className="lm-cast__head-eyebrow">Phase 4 · 인격 생성 (데모)</div>
              <h1 className="lm-cast__head-title">{TARGET_COUNT}명 인격을 만들고 있어요</h1>
              <div className="lm-cast__head-status">
                <span className="lm-cast__head-status-tag">demo</span>
                <span className="lm-cast__head-status-text">
                  데모 LLM 호출 시뮬레이션 · {elapsedSec}초 경과
                </span>
              </div>
            </div>
            <div className="lm-cast__head-actions">
              <Button kind="primary" disabled>생성 중…</Button>
            </div>
          </header>
          <div className="lm-cast__loading">
            <div className="lm-cast__loading-spinner" aria-hidden="true" />
            <p className="lm-cast__loading-hint">
              데모 모드 — 자료에서 핵심 인물·기관을 뽑고 {TARGET_COUNT}명 시민 인격을 빚는 중입니다.
            </p>
            <p className="lm-cast__loading-progress">
              현재 {agentCount} / {TARGET_COUNT} 명 완료
            </p>
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
            <div className="lm-cast__head-eyebrow">Phase 4 · 인격 추출 완료 (데모)</div>
            <h1 className="lm-cast__head-title">{agents.length}명의 인격이 모였습니다</h1>
            <div className="lm-cast__head-status">
              <span className="lm-cast__head-status-text">
                각 카드에 이름·역할·성향·관심 주제가 표시돼요. 준비됐다면 광장으로 입장하세요.
              </span>
            </div>
          </div>
          <div className="lm-cast__head-actions">
            <Button kind="primary" onClick={() => navigate('/demo/live')} trailing={<ArrowGlyph dir="right" />}>
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
