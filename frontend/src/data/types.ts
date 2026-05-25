// =====================================================================
// 라이트미로 — 데이터 타입
// data.js의 런타임 객체 셰이프를 그대로 타입으로 미러한다.
// (가이드 1.4 기반. 실제 모킹에서 쓰는 필드까지 보강.)
// 백엔드 API 응답 스키마(Pydantic)와 이 타입을 일치시키면 UI 변경 없이 실데이터 연결 가능.
// =====================================================================

export type RoleId =
  | 'broadcast'
  | 'investig'
  | 'columnist'
  | 'politician'
  | 'pundit'
  | 'citizen_m'
  | 'citizen_p'
  | 'citizen_x'
  | 'academic'
  | 'lawyer'
  | 'ngo'
  | 'corp';

export type GroupId = 'media' | 'politics' | 'citizen' | 'expert' | 'org';

export interface Role {
  id: RoleId;
  name: string;
  group: GroupId;
  color: string;
  cssVar: string;
}

export interface Group {
  id: GroupId;
  name: string;
  color: string;
}

export type Pose = 'P1' | 'P2' | 'P3' | 'P4' | 'P5' | 'P6';
export type Prop = 'O0' | 'O1' | 'O2' | 'O3' | 'O4' | 'O5';
export type Expr = 'E1' | 'E2' | 'E3';

export interface AvatarSpec {
  pose: Pose;
  prop: Prop;
  expr: Expr;
}

export interface Anchor {
  id: string;
  name: string;
  title: string;
  role: RoleId;
  avatar: AvatarSpec;
  ideology: number;
  baseInfluence: number;
  bio: string;
  isOrg?: boolean;
}

export type NodeKind = 'derived' | 'derived-viral' | 'anchor';

export interface PlazaNode {
  id: string;
  name: string | null;
  role: RoleId;
  kind: NodeKind;
  x: number;
  y: number;
  influence: number;
  color: string;
  anchor?: boolean;
  avatar?: AvatarSpec;
  bio?: string;
  // generatePlaza가 앵커 노드에 추가로 채우는 필드
  firstName?: string;
  title?: string;
}

export interface Quote {
  round: number;
  text: string;
  citations: number;
  propagations: number;
  cross: number;
}

// 시드 본문 토큰 (인격/키워드 마크업)
export interface SeedToken {
  t: string;
  tag?: 'policy' | 'anchor';
  anchorId?: string;
}

export interface SeedOptions {
  scale: string;
  rounds: number;
  package: string;
  participants: number;
}

export interface Seed {
  id: string;
  title: string;
  paragraphs: SeedToken[][];
  keywords: string[];
  options: SeedOptions;
  cost: number;
}

export interface RoundMeta {
  total: number;
  current: number;
  durations: { perRound: number; totalSec: number };
  counts: { utterances: number; citations: number; follows: number };
}

export interface KeyEvent {
  idx: number;
  text: string;
  value: string;
}

export interface DistributionBand {
  id: 'pro' | 'mid' | 'con';
  label: string;
  count: number;
  influence: number;
  color: string;
}

export interface Distribution {
  by: string;
  bands: DistributionBand[];
}

// ---- 리포트 ----
export type Confidence = 'low' | 'medium' | 'medium-high' | 'high';
export type BulletKind = 'pro' | 'con' | 'note';
export type Stance = 'pro' | 'mid' | 'con';

export interface ReportData {
  prediction: {
    headline: string;
    confidence: Confidence;
    bullets: { kind: BulletKind; text: string }[];
    recommendations: string[];
  };
  actions: Record<'CREATE_POST' | 'LIKE' | 'REPOST' | 'QUOTE_POST' | 'FOLLOW', number>;
  topics: {
    id: string;
    name: string;
    hits: number;
    dominant: Stance;
    share: { pro: number; mid: number; con: number };
  }[];
  series: { round: number; utterances: number; pro: number; mid: number; con: number }[];
  anchors: (Anchor & {
    citations: number;
    propagations: number;
    crossCites: number;
    followerGain: number;
  })[];
  social: {
    initialFollows: number;
    finalFollows: number;
    newFollows: number;
    cliqueCount: number;
    bridgeAgents: number;
    rewireRate: number;
  };
  cost: { tokens: number; calls: number; fallbackPct: number; latencyAvg: number; krw: number };
  total: number;
  counts: RoundMeta['counts'];
}

// ---- 액션 (라이브/스트림) ----
export type ActionType = 'CREATE_POST' | 'QUOTE_POST' | 'REPOST' | 'LIKE' | 'FOLLOW';

export interface Action {
  round: number;
  agentId: string;
  type: ActionType;
  content?: string;
  targetId?: string;
  // 라이브 화면의 누적 표시용(앵커 발언)
  citesAccum?: number;
  repostsAccum?: number;
  _i?: number;
}

// 라이브 화면 에이전트
export interface Agent {
  id: string;
  name: string;
  short: string;
  role: RoleId;
  kind: NodeKind;
  avatar?: AvatarSpec;
}

export interface AgentRegistry {
  list: Agent[];
  byId: Record<string, Agent>;
}
