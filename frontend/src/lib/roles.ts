// =====================================================================
// 백엔드 ↔ 프론트 RoleId 매핑.
//
// 백엔드 `/api/plazas/{id}/agents` 가 돌려주는 `role` 은 Phase 1 ontology 의
// `AgentProfile.entity_type` raw 문자열이다 (예: "AIRegulationPolicy",
// "IndustryGroup", "Researcher"). 백엔드는 enum 으로 안 좁히기로 결정했고
// (docs/api/contract.md /agents 섹션 SSoT), 프론트가 본 모듈로 mock 의 12
// `RoleId` 중 하나로 매핑한다.
//
// contract.md 가 제안한 6 카테고리 (policy / industry / expert / civic /
// media / other) 는 프론트 mock 12 enum 의 의미 있는 대표값으로 변환한다 —
// mock 의 시각적 풍부함 (색·이름·성향) 을 유지하면서 백엔드 응답을 흡수.
//
// 새 `entity_type` 이 ontology 측에 도입되면 본 표만 갱신. 미지정 키는
// `_FALLBACK` (시민·실용) 으로 떨어진다 — 색·이름이 있어 화면이 빈 박스로
// 보이지 않는다.
// =====================================================================

import type { AvatarSpec, Expr, Pose, Prop, RoleId } from '@/data/types';

const _FALLBACK: RoleId = 'citizen_x';

const ENTITY_TYPE_TO_ROLE_ID: Record<string, RoleId> = {
  // 실 백엔드 (Phase 1 dev fixture) 가 보내는 짧은 라벨.
  Journalist: 'broadcast',
  Academic: 'academic',
  Citizen: 'citizen_m',
  citizen: 'citizen_m',
  Politician: 'politician',
  Corporation: 'corp',
  Activist: 'ngo',
  // contract.md "policy" → 정치인
  AIRegulationPolicy: 'politician',
  Government: 'politician',
  GovernmentAgency: 'politician',
  // contract.md "industry" → 기업
  IndustryGroup: 'corp',
  IndustryActor: 'corp',
  Company: 'corp',
  // contract.md "expert" → 학자
  Researcher: 'academic',
  AcademicInstitution: 'academic',
  // contract.md "civic" → 시민단체
  CivicGroup: 'ngo',
  CivilSocietyOrganization: 'ngo',
  // contract.md "media" → 방송기자 (mock 의 media 그룹 중 가장 일반적)
  Media: 'broadcast',
  MediaOutlet: 'broadcast',
};

export function mapBackendRoleToRoleId(entityType: string): RoleId {
  return ENTITY_TYPE_TO_ROLE_ID[entityType] ?? _FALLBACK;
}

// --------------------------------------------------------------------
// avatar_seed (uint32, sha256(agent_id)[:4]) → AvatarSpec.
// 백엔드가 시드만 권위로 제공하고 (해시 알고리즘은 contract 로 lock-in)
// pose/prop/expr 추출 정책은 프론트가 결정. 같은 시드면 같은 결과 — reload/
// 재연결에서 아바타가 안 튄다.
// --------------------------------------------------------------------
const _POSES: Pose[] = ['P1', 'P2', 'P3', 'P4', 'P5', 'P6'];
const _PROPS: Prop[] = ['O0', 'O1', 'O2', 'O3', 'O4', 'O5'];
const _EXPRS: Expr[] = ['E1', 'E2', 'E3'];

export function avatarFromSeed(seed: number): AvatarSpec {
  // 32-bit 시드의 각 8-bit 청크로 pose/prop/expr 결정 — 충돌 가능성 낮고
  // 비트 분포 활용.
  return {
    pose: _POSES[seed % _POSES.length],
    prop: _PROPS[(seed >> 8) % _PROPS.length],
    expr: _EXPRS[(seed >> 16) % _EXPRS.length],
  };
}
