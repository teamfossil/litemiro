// =====================================================================
// 라이트미로 — 데이터 모듈 (mock)
// data.js를 ES 모듈로 이식. 로직은 원본 그대로, 타입만 보강.
// 실 제품에서는 이 자리에 API 응답이 들어간다. 셰이프는 그대로 유지.
// (HANDOFF §2.3 — window.LM.* → 이 모듈의 named export / lm 집합 객체)
// =====================================================================

import type {
  Anchor,
  Distribution,
  Group,
  GroupId,
  KeyEvent,
  PlazaNode,
  Quote,
  Role,
  RoleId,
  RoundMeta,
  Seed,
} from './types';

// ---------- ROLE REGISTRY ----------
export const ROLES: Role[] = [
  { id: 'broadcast', name: '방송기자', group: 'media', color: '#B85138', cssVar: '--r-broadcast' },
  { id: 'investig', name: '탐사기자', group: 'media', color: '#C77B4F', cssVar: '--r-investig' },
  { id: 'columnist', name: '칼럼니스트', group: 'media', color: '#C9923D', cssVar: '--r-columnist' },
  { id: 'politician', name: '정치인', group: 'politics', color: '#A68240', cssVar: '--r-politician' },
  { id: 'pundit', name: '평론가', group: 'politics', color: '#8F6B3D', cssVar: '--r-pundit' },
  { id: 'citizen_m', name: '시민·온건', group: 'citizen', color: '#6D8FA6', cssVar: '--r-citizen-m' },
  { id: 'citizen_p', name: '시민·진보', group: 'citizen', color: '#4F7591', cssVar: '--r-citizen-p' },
  { id: 'citizen_x', name: '시민·실용', group: 'citizen', color: '#7896A0', cssVar: '--r-citizen-x' },
  { id: 'academic', name: '학자', group: 'expert', color: '#4F7B6E', cssVar: '--r-academic' },
  { id: 'lawyer', name: '법조', group: 'expert', color: '#6E8770', cssVar: '--r-lawyer' },
  { id: 'ngo', name: '시민단체', group: 'org', color: '#8B8170', cssVar: '--r-ngo' },
  { id: 'corp', name: '기업', group: 'org', color: '#6E6D7D', cssVar: '--r-corp' },
];

export const ROLE_BY_ID: Record<RoleId, Role> = Object.fromEntries(
  ROLES.map((r) => [r.id, r]),
) as Record<RoleId, Role>;

export const GROUPS: Group[] = [
  { id: 'media', name: '미디어', color: '#B85138' },
  { id: 'politics', name: '정치', color: '#A68240' },
  { id: 'citizen', name: '시민', color: '#4F7591' },
  { id: 'expert', name: '전문가', color: '#4F7B6E' },
  { id: 'org', name: '조직', color: '#8B8170' },
];

// 역할별 이데올로기 편향 (-1..1, 좌=비판적, 우=우호적). 위치 가우시안 중심.
export const ROLE_IDEOLOGY: Record<RoleId, number> = {
  broadcast: 0.55,
  investig: -0.2,
  columnist: 0.4,
  politician: 0.35,
  pundit: 0.5,
  citizen_m: -0.1,
  citizen_p: -0.65,
  citizen_x: 0.05,
  academic: -0.3,
  lawyer: 0.1,
  ngo: -0.55,
  corp: 0.6,
};

// 카운트 가중치. 광장에 시민이 많고 미디어가 소수.
export const ROLE_COUNT_WEIGHT: Record<RoleId, number> = {
  broadcast: 2,
  investig: 2,
  columnist: 3,
  politician: 3,
  pundit: 2,
  citizen_m: 8,
  citizen_p: 9,
  citizen_x: 6,
  academic: 3,
  lawyer: 2,
  ngo: 3,
  corp: 2,
};

// ---------- SEEDED RNG ----------
export function mulberry32(seed: number): () => number {
  return function () {
    seed |= 0;
    seed = (seed + 0x6d2b79f5) | 0;
    let t = seed;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export function gauss(rng: () => number): number {
  let u = 0;
  let v = 0;
  while (u === 0) u = rng();
  while (v === 0) v = rng();
  const z = Math.sqrt(-2.0 * Math.log(u)) * Math.cos(2.0 * Math.PI * v);
  return Math.max(-3, Math.min(3, z));
}

// ---------- COLOR — 같은 역할 안에서 명도 변주 ----------
export function shadeHex(hex: string, delta: number): string {
  // delta ∈ [-1..1]. 음수=어둡게, 양수=밝게. 채도 변경 없이 흰/검과 보간.
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  const step = (c: number) =>
    delta >= 0 ? Math.round(c + (255 - c) * delta) : Math.round(c * (1 + delta));
  const to = (v: number) => Math.max(0, Math.min(255, v)).toString(16).padStart(2, '0');
  return '#' + to(step(r)) + to(step(g)) + to(step(b));
}

// ---------- 헬퍼 ----------
export function clamp(v: number, a: number, b: number): number {
  return Math.max(a, Math.min(b, v));
}

export function nodeRadius(influence: number, base = 2.5, max = 28): number {
  return base + Math.pow(influence, 1.4) * max;
}

// ---------- ANCHORS — 시드에서 추출된 이름 있는 인격들 ----------
export const ANCHORS: Anchor[] = [
  {
    id: 'cm',
    name: '최영민',
    title: '기자',
    role: 'broadcast',
    avatar: { pose: 'P1', prop: 'O1', expr: 'E3' },
    ideology: 0.78,
    baseInfluence: 1.0,
    bio: '경제 산업부, 12년 차. 노동·산업 트랜지션을 주로 다룬다.',
  },
  {
    id: 'hj',
    name: '한지영',
    title: '칼럼니스트',
    role: 'columnist',
    avatar: { pose: 'P4', prop: 'O2', expr: 'E1' },
    ideology: 0.18,
    baseInfluence: 0.78,
    bio: '주간지 〈광장의 일〉 고정 칼럼. 돌봄·교육 사회학.',
  },
  {
    id: 'js',
    name: '정세훈',
    title: '의원',
    role: 'politician',
    avatar: { pose: 'P2', prop: 'O5', expr: 'E1' },
    ideology: 0.32,
    baseInfluence: 0.84,
    bio: '재선. 근로기준법 개정안 대표 발의.',
  },
  {
    id: 'pk',
    name: '박서경',
    title: '교수',
    role: 'academic',
    avatar: { pose: 'P4', prop: 'O1', expr: 'E1' },
    ideology: 0.44,
    baseInfluence: 0.62,
    bio: '노동경제학. OECD 정책 비교 연구.',
  },
  {
    id: 'no',
    name: '전국노동연대',
    title: '시민단체',
    role: 'ngo',
    avatar: { pose: 'P2', prop: 'O3', expr: 'E1' },
    ideology: 0.12,
    baseInfluence: 0.55,
    bio: '대변 발언자 김민서. 산업별 시범사업 확대 요구 성명.',
    isOrg: true,
  },
];

// ---------- SEED — 광장의 입력 문서 ----------
export const SEED: Seed = {
  id: 'seed-week4',
  title: '주 4일제 도입을 둘러싼 한국 사회의 입장 분포.',
  paragraphs: [
    [
      { t: '지난 정기국회에서 ' },
      { t: '근로기준법 개정안', tag: 'policy' },
      { t: '이 발의되며, ' },
      { t: '정세훈 의원', tag: 'anchor', anchorId: 'js' },
      { t: '이 주도하는 비판적 진영과 산업계를 대변하는 우호적 진영이 부딪히고 있다.' },
    ],
    [
      { t: '방송에서는 ' },
      { t: '최영민 기자', tag: 'anchor', anchorId: 'cm' },
      { t: '의 비판 보도가 화제를 모았고, ' },
      { t: '한지영', tag: 'anchor', anchorId: 'hj' },
      { t: '은 칼럼을 통해 보육·돌봄 노동의 관점에서 도입 필요성을 강조했다.' },
    ],
    [
      { t: '학계에서는 ' },
      { t: '박서경 교수', tag: 'anchor', anchorId: 'pk' },
      { t: '가 OECD 사례를 인용해 산업별 트랜지션 설계가 선행되어야 함을 짚었으며, ' },
      { t: '전국노동연대', tag: 'anchor', anchorId: 'no' },
      { t: '는 시범 사업 확대를 요구하는 성명을 냈다.' },
    ],
  ],
  keywords: ['주4일제', '근로기준법', '보육', '트랜지션', '시범사업', 'OECD', '돌봄'],
  options: {
    scale: 'mid',
    rounds: 40,
    package: 'kr-news',
    participants: 312,
  },
  cost: 1240,
};

// ---------- ROUND META ----------
export const ROUND_META: RoundMeta = {
  total: 50,
  current: 50,
  durations: { perRound: 14, totalSec: 720 },
  counts: { utterances: 3624, citations: 10240, follows: 2310 },
};

// ---------- 노드 생성 ----------
// 시드 RNG로 생성한 312명. 같은 시드 → 같은 광장.
export function generatePlaza({ seed = 42, n = 312 }: { seed?: number; n?: number } = {}): PlazaNode[] {
  const rng = mulberry32(seed);
  const totalW = Object.values(ROLE_COUNT_WEIGHT).reduce((a, b) => a + b, 0);
  const nodes: PlazaNode[] = [];

  // 1) 군중(derived) 인격 — 익명
  for (let i = 0; i < n - ANCHORS.length; i++) {
    let pick = rng() * totalW;
    let roleId: RoleId = 'citizen_m';
    for (const [id, w] of Object.entries(ROLE_COUNT_WEIGHT)) {
      pick -= w;
      if (pick <= 0) {
        roleId = id as RoleId;
        break;
      }
    }
    const ideo = ROLE_IDEOLOGY[roleId] ?? 0;
    // 가로축 = 입장. 가우시안 분포로 역할 평균 주변 흩뿌림.
    let x = 0.5 + ideo * 0.36 + gauss(rng) * 0.09;
    x = clamp(x, 0.03, 0.97);
    const y = 0.18 + rng() * 0.72;

    // 영향력 — 긴 꼬리 분포. 군중 노드는 대부분 낮음.
    const roll = rng();
    let inf: number;
    if (roll < 0.005) inf = 0.85 + rng() * 0.15;
    else if (roll < 0.02) inf = 0.45 + rng() * 0.25;
    else if (roll < 0.08) inf = 0.2 + rng() * 0.18;
    else if (roll < 0.25) inf = 0.08 + rng() * 0.12;
    else inf = 0.02 + rng() * 0.08;

    // 같은 역할 안에서 개체별 명도 변주.
    const shade = (rng() - 0.5) * 0.3;
    const color = shadeHex(ROLE_BY_ID[roleId].color, shade);

    nodes.push({
      id: `n${i}`,
      name: null,
      role: roleId,
      kind: 'derived',
      x,
      y,
      influence: inf,
      color,
    });
  }

  // 2) 바이럴 derived — 영향력이 군중 평균보다 크게 튀어오른 익명 시민.
  const viralIdx = nodes.findIndex((nd) => nd.role === 'citizen_p' && nd.influence > 0.1);
  if (viralIdx > -1) {
    nodes[viralIdx] = {
      ...nodes[viralIdx],
      name: '시민·익명 #47',
      kind: 'derived-viral',
      influence: 0.66,
      x: 0.34,
      y: 0.62,
    };
  }

  // 3) 앵커 — 시드에서 추출된 5명. 큰 노드 + 이름 + 표정/소품.
  ANCHORS.forEach((a, i) => {
    const ideo = a.ideology;
    const x = 0.5 + (ideo - 0.5) * 0.72; // 좀 더 양극단으로
    const yArr = [0.5, 0.34, 0.26, 0.2, 0.78];
    nodes.push({
      id: a.id,
      name: `${a.name}${a.isOrg ? '' : ' ' + a.title}`,
      firstName: a.name,
      title: a.title,
      role: a.role,
      kind: 'anchor',
      x,
      y: yArr[i % yArr.length],
      influence: a.baseInfluence,
      color: shadeHex(ROLE_BY_ID[a.role].color, -0.05),
      anchor: true,
      avatar: a.avatar,
      bio: a.bio,
    });
  });

  return nodes;
}

// ---------- 발언(quotes) — 앵커별 ----------
export const QUOTES: Record<string, Quote[]> = {
  cm: [
    { round: 12, text: '근로시간 단축이 생산성 보전 없이 갈 때 어떤 산업이 가장 먼저 흔들리는가.', citations: 31, propagations: 47, cross: 23 },
    { round: 23, text: '통계는 평균을 보지만 시급제 노동자는 평균 위에 살지 않습니다.', citations: 22, propagations: 39, cross: 18 },
    { round: 31, text: '선언적 도입이 아니라, 산업별 트랜지션 설계가 먼저입니다.', citations: 33, propagations: 51, cross: 28 },
  ],
  hj: [
    { round: 8, text: '돌봄을 떠받치는 노동의 시간이 1시간 늘면, 한 가족의 저녁이 살아납니다.', citations: 28, propagations: 64, cross: 22 },
    { round: 19, text: '보육교사 친구는 그냥 하루만 더 쉬어도 사람답게 산다고 그래요.', citations: 19, propagations: 41, cross: 14 },
  ],
  js: [
    { round: 4, text: '발의안의 핵심은 강제가 아니라 권리 명시입니다. 근로자가 선택할 수 있어야 합니다.', citations: 24, propagations: 38, cross: 19 },
    { round: 27, text: '제도가 만든 결과만 본다면 우리는 다시 같은 자리에 서게 됩니다.', citations: 21, propagations: 33, cross: 17 },
  ],
  pk: [
    { round: 15, text: 'OECD 평균은 평균일 뿐. 한국 노동시장의 진입·이행 구조를 함께 봐야 합니다.', citations: 17, propagations: 28, cross: 12 },
  ],
  no: [
    { round: 22, text: '시범사업 3년치 자료를 공개합니다. 결과로 말합시다.', citations: 14, propagations: 30, cross: 11 },
  ],
  'n-viral-47': [
    { round: 14, text: '우리 동네 어린이집 보육교사 친구는 그냥, 하루만 더 쉬어도 사람답게 산다고 그래요.', citations: 18, propagations: 184, cross: 92 },
  ],
};

// ---------- KEY EVENTS — 결과 리포트용 ----------
export const KEY_EVENTS: KeyEvent[] = [
  { idx: 1, text: '미디어 앵커 3명이 <b>교차 인용 62%</b>를 만들었어요.', value: '62%' },
  { idx: 2, text: '시민 진영에서 <b>derived #47</b>이 바이럴, 영향력 상위 9위로 승격.', value: '+184' },
  { idx: 3, text: '중립 클러스터는 <b>40 라운드 내내 의견을 유지</b>했어요.', value: 'stable' },
  { idx: 4, text: '진영을 가로지른 발화의 <b>78%</b>가 R20 이후에 집중되었어요.', value: 'R20+' },
];

// ---------- DISTRIBUTION — 입장별 인원수 vs 화제성 ----------
export const DISTRIBUTION: Distribution = {
  by: 'ideology',
  bands: [
    { id: 'pro', label: '비판적', count: 0.38, influence: 0.22, color: '#4F7591' },
    { id: 'mid', label: '중립', count: 0.23, influence: 0.12, color: '#8B8170' },
    { id: 'con', label: '우호적', count: 0.39, influence: 0.66, color: '#C77B4F' },
  ],
};

// ---------- 집합 export ----------
// window.LM과 동일한 셰이프. 화면 코드에서 window.LM.* → lm.* 로 1:1 치환.
export const lm = {
  ROLES,
  ROLE_BY_ID,
  GROUPS,
  ROLE_IDEOLOGY,
  ROLE_COUNT_WEIGHT,
  ANCHORS,
  SEED,
  ROUND_META,
  QUOTES,
  KEY_EVENTS,
  DISTRIBUTION,
  generatePlaza,
  nodeRadius,
  shadeHex,
  mulberry32,
  gauss,
  clamp,
};

export default lm;
