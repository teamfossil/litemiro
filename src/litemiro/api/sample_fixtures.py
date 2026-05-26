"""POST /plazas 가 ontology path 를 받지 못했을 때 채워줄 dev 폴백.

프론트 Seed 화면은 자료 업로드 UI 가 없어 항상 같은 sample 로 호출한다. 호출
측이 every call 마다 dummy path 를 박는 게 어색해서, 라우트가 두 경로를
optional 로 받고 omit 시 본 모듈의 기본 fixture 로 채운다.

기본 fixture 는 repo 의 `tests/data/sample_ontology_*.json` — Phase 1 산출
스키마를 그대로 통과하는 같은 파일을 재사용한다 (smoke 테스트와 동일 입력으로
프론트 wiring 검증 가능). 패키징/배포 환경에서 파일이 존재하지 않으면 경로는
그대로 비존재 Path 로 전달되어 후속 /agents·sim 단계에서 정상 경로 미지정과
동일하게 404/실패로 떨어진다 — 어디까지나 dev 편의용 폴백이지 프로덕션 default
아님.
"""

from __future__ import annotations

from pathlib import Path

# src/litemiro/api/sample_fixtures.py 기준 4 단계 위가 repo root.
_REPO_ROOT = Path(__file__).resolve().parents[3]
_FIXTURE_DIR = _REPO_ROOT / "tests" / "data"

DEFAULT_ONTOLOGY_A_PATH: Path = _FIXTURE_DIR / "sample_ontology_a.json"
DEFAULT_ONTOLOGY_B_PATH: Path = _FIXTURE_DIR / "sample_ontology_b.json"


__all__ = [
    "DEFAULT_ONTOLOGY_A_PATH",
    "DEFAULT_ONTOLOGY_B_PATH",
]
