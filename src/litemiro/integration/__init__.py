"""Phase 1 ↔ Phase 2 경계 어댑터.

`OntologyLoader` 가 듀얼 온톨로지 JSON 산출을 Phase 2 입력 객체
(`Agent[]` / `SocialGraph`) 로 변환한다. 계약은
`docs/integration/phase1-2-contract.md` 참조.
"""

from __future__ import annotations

from litemiro.integration.ontology_loader import OntologyLoader

__all__ = ["OntologyLoader"]
