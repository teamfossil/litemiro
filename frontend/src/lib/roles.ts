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

import type { RoleId } from '@/data/types';

const _FALLBACK: RoleId = 'citizen_x';

const ENTITY_TYPE_TO_ROLE_ID: Record<string, RoleId> = {
  // contract.md "policy" → 정치인
  AIRegulationPolicy: 'politician',
  Government: 'politician',
  // contract.md "industry" → 기업
  IndustryGroup: 'corp',
  Company: 'corp',
  // contract.md "expert" → 학자
  Researcher: 'academic',
  // contract.md "civic" → 시민단체
  CivicGroup: 'ngo',
  // contract.md "media" → 방송기자 (mock 의 media 그룹 중 가장 일반적)
  Media: 'broadcast',
};

export function mapBackendRoleToRoleId(entityType: string): RoleId {
  return ENTITY_TYPE_TO_ROLE_ID[entityType] ?? _FALLBACK;
}
