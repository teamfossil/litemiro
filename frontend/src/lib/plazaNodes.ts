// =====================================================================
// plazaNodes — positions + agents 합성 유틸. Plaza.tsx / Report.tsx 공용.
// Live.tsx 의 stanceColor / buildLiveNodes 와 동일 인코딩을 유지한다.
// =====================================================================

import type { PlazaNode } from '@/data/types';
import type { PlazaAgentItem, PlazaPositionAgent } from '@/api/client';
import { mapBackendRoleToRoleId } from '@/lib/roles';

// --------------------------------------------------------------------
// 진영(stance) 버킷 — Live 와 같은 경계(0.4/0.6). 필터·라벨·색 분류 공용.
// --------------------------------------------------------------------
export type StanceBucket = 'critical' | 'neutral' | 'supportive';

export const STANCE_BUCKETS: { id: StanceBucket; name: string; color: string }[] = [
  { id: 'critical', name: '비판', color: '#c75c54' },
  { id: 'neutral', name: '중립', color: '#a99f88' },
  { id: 'supportive', name: '우호', color: '#5b87b3' },
];

// stance(0~1) → 진영 색. Live.tsx 의 stanceColor 와 동일 (경계 0.4 / 0.6).
export function stanceColor(stance: number): string {
  if (stance < 0.4) return '#c75c54'; // 비판
  if (stance > 0.6) return '#5b87b3'; // 우호
  return '#a99f88'; // 중립
}

export function stanceBucket(stance: number): StanceBucket {
  if (stance < 0.4) return 'critical';
  if (stance > 0.6) return 'supportive';
  return 'neutral';
}

export function stanceLabel(stance: number): string {
  if (stance < 0.4) return '비판적';
  if (stance > 0.6) return '우호적';
  return '중립';
}

// x = ideology(진보↔보수) 라벨.
export function ideologyLabel(x: number): string {
  if (x < 0.32) return '진보';
  if (x < 0.42) return '진보-중도';
  if (x < 0.58) return '중도';
  if (x < 0.68) return '중도-보수';
  return '보수';
}

// --------------------------------------------------------------------
// buildPlazaNodes — positions(라운드별 x/y/size) + agents(정적 stance/이름) 합성.
// Live 의 buildLiveNodes 와 같은 정규화:
//   x         = ideology (진보↔보수, 정규화 없음)
//   y         = 발화량(size) min-max 정규화  → 세로 위치에 사용
//   influence = 받은 호응(y) sqrt 스케일 [0,1] → 반지름에 사용
//   color     = stanceColor(agent.stance)
// --------------------------------------------------------------------
export function buildPlazaNodes(
  agents: PlazaAgentItem[],
  positions: PlazaPositionAgent[],
): PlazaNode[] {
  if (positions.length === 0) return [];
  const sizes = positions.map((p) => p.size);
  const minS = Math.min(...sizes);
  const maxS = Math.max(...sizes);
  // 받은 호응은 소수만 >0 인 long-tail → sqrt 로 소수 인플루언서만 크게 (Live 동일).
  const maxRecv = Math.max(1, ...positions.map((p) => p.y));
  const agentMap = new Map(agents.map((a) => [a.id, a]));
  return positions.map((p): PlazaNode => {
    const a = agentMap.get(p.id);
    const stance = a?.stance ?? 0.5;
    return {
      id: p.id,
      name: a?.name ?? null,
      role: mapBackendRoleToRoleId(a?.role ?? ''),
      kind: 'anchor',
      x: p.x,
      y: maxS > minS ? (p.size - minS) / (maxS - minS) : 0.5,
      influence: Math.sqrt(p.y / maxRecv),
      color: stanceColor(stance),
      stance,
      anchor: true,
    };
  });
}
