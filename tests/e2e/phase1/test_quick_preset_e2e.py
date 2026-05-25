"""Phase 1 quick preset e2e — mock LLM, full pipeline, Pydantic + Schema validation.

Runs the complete OntologyPipeline with ``sample_document.txt`` and a
deterministic mock LLM, then validates every contract the downstream
Phase 2 loader depends on.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from litemiro.phase1.models import OntologyA, OntologyB, Preset
from litemiro.phase1.pipeline import OntologyPipeline, PipelineConfig
from litemiro.phase1.serializer import OntologySerializer
from litemiro.phase1.validator import OntologyValidator

DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
SAMPLE_DOC = DATA_DIR / "sample_document.txt"

# ── Mock LLM responses (matched to sample_document.txt) ────────────

ONTOLOGY_RESP = json.dumps(
    {
        "entity_types": [
            {
                "name": "GovernmentAgency",
                "description": "정부 부처 또는 공공 기관",
                "attributes": ["name", "jurisdiction", "role"],
            },
            {
                "name": "Politician",
                "description": "정치인 또는 고위 공직자",
                "attributes": ["name", "position", "party"],
            },
            {
                "name": "TechCompany",
                "description": "기술 기업",
                "attributes": ["name", "sector", "products"],
            },
            {
                "name": "ResearchInstitution",
                "description": "학술 또는 연구 기관",
                "attributes": ["name", "field"],
            },
            {
                "name": "CivilSocietyOrg",
                "description": "시민 사회 단체",
                "attributes": ["name", "mission", "stance"],
            },
            {
                "name": "MediaOrganization",
                "description": "언론 매체",
                "attributes": ["name", "type", "orientation"],
            },
            {
                "name": "Journalist",
                "description": "기자",
                "attributes": ["name", "affiliation", "beat"],
            },
            {
                "name": "Researcher",
                "description": "연구자",
                "attributes": ["name", "institution", "specialty"],
            },
            {
                "name": "Activist",
                "description": "시민 활동가",
                "attributes": ["name", "organization", "focus"],
            },
            {
                "name": "IndustryAlliance",
                "description": "산업 연합",
                "attributes": ["name", "members"],
            },
        ],
        "edge_types": [
            {
                "name": "LEADS",
                "source": "Politician",
                "target": "GovernmentAgency",
                "description": "기관장",
            },
            {
                "name": "WORKS_FOR",
                "source": "Journalist",
                "target": "MediaOrganization",
                "description": "소속",
            },
            {
                "name": "AFFILIATED_WITH",
                "source": "Researcher",
                "target": "ResearchInstitution",
                "description": "소속 연구기관",
            },
            {
                "name": "OPPOSES",
                "source": "CivilSocietyOrg",
                "target": "GovernmentAgency",
                "description": "정책 반대",
            },
            {
                "name": "SUPPORTS",
                "source": "TechCompany",
                "target": "IndustryAlliance",
                "description": "지지",
            },
            {
                "name": "REGULATES",
                "source": "GovernmentAgency",
                "target": "TechCompany",
                "description": "규제",
            },
            {
                "name": "REPORTS_ON",
                "source": "Journalist",
                "target": "GovernmentAgency",
                "description": "보도",
            },
            {
                "name": "ADVOCATES",
                "source": "Activist",
                "target": "CivilSocietyOrg",
                "description": "활동",
            },
        ],
    },
    ensure_ascii=False,
)

EXTRACT_RESP = json.dumps(
    {
        "entities": [
            {
                "id": "gov_msit",
                "type": "GovernmentAgency",
                "name": "과학기술정보통신부",
                "attributes": {"jurisdiction": "AI 규제"},
                "summary": "AI 규제의 주무 부처, AI 기본법 시행령 마련",
                "source_chunks": [0],
            },
            {
                "id": "gov_pipc",
                "type": "GovernmentAgency",
                "name": "개인정보보호위원회",
                "attributes": {"jurisdiction": "개인정보"},
                "summary": "AI 개인정보 처리 가이드라인 발표",
                "source_chunks": [0],
            },
            {
                "id": "pol_kim",
                "type": "Politician",
                "name": "김태호",
                "attributes": {"position": "장관"},
                "summary": "과기부 장관, 혁신과 규제 균형 강조",
                "source_chunks": [0],
            },
            {
                "id": "pol_park",
                "type": "Politician",
                "name": "박영선",
                "attributes": {"position": "위원장"},
                "summary": "개인정보위 위원장, GDPR 참고 규제 강화 주장",
                "source_chunks": [0],
            },
            {
                "id": "corp_naver",
                "type": "TechCompany",
                "name": "네이버",
                "attributes": {"sector": "AI", "products": "HyperCLOVA"},
                "summary": "자체 AI 모델 운영, 자율규제 선호",
                "source_chunks": [0, 1],
            },
            {
                "id": "corp_kakao",
                "type": "TechCompany",
                "name": "카카오",
                "attributes": {"sector": "AI"},
                "summary": "AI 윤리 위원회 설치, 자체 윤리 가이드라인 운영",
                "source_chunks": [0],
            },
            {
                "id": "corp_samsung",
                "type": "TechCompany",
                "name": "삼성전자",
                "attributes": {"sector": "반도체"},
                "summary": "AI 칩 개발, 하드웨어 안전성 강조",
                "source_chunks": [0],
            },
            {
                "id": "alliance_kai",
                "type": "IndustryAlliance",
                "name": "KAI Alliance",
                "attributes": {},
                "summary": "중소 AI 스타트업 연합, 규제 비용 부담 우려",
                "source_chunks": [0],
            },
            {
                "id": "res_snu",
                "type": "ResearchInstitution",
                "name": "서울대 AI 연구원",
                "attributes": {"field": "AI"},
                "summary": "AI 안전성 연구",
                "source_chunks": [1],
            },
            {
                "id": "res_kaist",
                "type": "ResearchInstitution",
                "name": "KAIST AI 대학원",
                "attributes": {"field": "AI"},
                "summary": "AI 위험성 연구",
                "source_chunks": [1],
            },
            {
                "id": "res_etri",
                "type": "ResearchInstitution",
                "name": "ETRI",
                "attributes": {"field": "AI 신뢰성"},
                "summary": "AI 신뢰성 평가 프레임워크 개발",
                "source_chunks": [1],
            },
            {
                "id": "researcher_jung",
                "type": "Researcher",
                "name": "정민수",
                "attributes": {"institution": "서울대"},
                "summary": "AI 위험성 과장 입장",
                "source_chunks": [1],
            },
            {
                "id": "researcher_han",
                "type": "Researcher",
                "name": "한소영",
                "attributes": {"institution": "KAIST"},
                "summary": "선제적 규제 지지",
                "source_chunks": [1],
            },
            {
                "id": "cso_pam",
                "type": "CivilSocietyOrg",
                "name": "참여연대",
                "attributes": {"mission": "디지털 권리"},
                "summary": "AI 차별과 편향 문제 지적",
                "source_chunks": [1],
            },
            {
                "id": "cso_fair_ai",
                "type": "CivilSocietyOrg",
                "name": "공정한AI연대",
                "attributes": {"mission": "AI 공정성"},
                "summary": "AI 의사결정 설명가능성 의무화 주장",
                "source_chunks": [1],
            },
            {
                "id": "cso_digital",
                "type": "CivilSocietyOrg",
                "name": "디지털자유연대",
                "attributes": {"mission": "기술 자유주의"},
                "summary": "규제 최소화 옹호",
                "source_chunks": [1],
            },
            {
                "id": "activist_choi",
                "type": "Activist",
                "name": "최수현",
                "attributes": {"organization": "참여연대"},
                "summary": "알고리즘 투명성 요구",
                "source_chunks": [1],
            },
            {
                "id": "media_hankyoreh",
                "type": "MediaOrganization",
                "name": "한겨레신문",
                "attributes": {"type": "신문", "orientation": "진보"},
                "summary": "AI 정책 비판적 보도",
                "source_chunks": [1],
            },
            {
                "id": "media_chosun",
                "type": "MediaOrganization",
                "name": "조선일보",
                "attributes": {"type": "신문", "orientation": "보수"},
                "summary": "AI 산업 육성 강조 보도",
                "source_chunks": [1],
            },
            {
                "id": "media_techwatch",
                "type": "MediaOrganization",
                "name": "테크워치",
                "attributes": {"type": "IT 전문 매체"},
                "summary": "기술적 관점 중립 보도",
                "source_chunks": [1],
            },
            {
                "id": "journalist_kim_ys",
                "type": "Journalist",
                "name": "김영수",
                "attributes": {"affiliation": "한겨레신문", "beat": "AI 정책"},
                "summary": "AI 정책 비판적 보도",
                "source_chunks": [1],
            },
            {
                "id": "journalist_park_jm",
                "type": "Journalist",
                "name": "박정민",
                "attributes": {"affiliation": "조선일보", "beat": "AI 산업"},
                "summary": "AI 산업 육성 필요성 강조",
                "source_chunks": [1],
            },
            {
                "id": "journalist_lee",
                "type": "Journalist",
                "name": "이하늘",
                "attributes": {"affiliation": "테크워치", "beat": "기술"},
                "summary": "기술적 관점 중립 보도",
                "source_chunks": [1],
            },
            {
                "id": "researcher_lee_js",
                "type": "Researcher",
                "name": "이준석",
                "attributes": {"institution": "네이버 AI 랩"},
                "summary": "과도한 규제 우려",
                "source_chunks": [0],
            },
        ],
        "relationships": [
            {
                "source": "pol_kim",
                "target": "gov_msit",
                "type": "LEADS",
                "description": "과기부 장관",
            },
            {
                "source": "pol_park",
                "target": "gov_pipc",
                "type": "LEADS",
                "description": "개인정보위 위원장",
            },
            {
                "source": "journalist_kim_ys",
                "target": "media_hankyoreh",
                "type": "WORKS_FOR",
                "description": "한겨레 소속",
            },
            {
                "source": "journalist_park_jm",
                "target": "media_chosun",
                "type": "WORKS_FOR",
                "description": "조선일보 소속",
            },
            {
                "source": "journalist_lee",
                "target": "media_techwatch",
                "type": "WORKS_FOR",
                "description": "테크워치 소속",
            },
            {
                "source": "researcher_jung",
                "target": "res_snu",
                "type": "AFFILIATED_WITH",
                "description": "서울대 소속",
            },
            {
                "source": "researcher_han",
                "target": "res_kaist",
                "type": "AFFILIATED_WITH",
                "description": "KAIST 소속",
            },
            {
                "source": "researcher_lee_js",
                "target": "corp_naver",
                "type": "AFFILIATED_WITH",
                "description": "네이버 AI 랩 소장",
            },
            {
                "source": "activist_choi",
                "target": "cso_pam",
                "type": "ADVOCATES",
                "description": "참여연대 활동가",
            },
            {
                "source": "gov_msit",
                "target": "corp_naver",
                "type": "REGULATES",
                "description": "AI 규제",
            },
            {
                "source": "gov_msit",
                "target": "corp_kakao",
                "type": "REGULATES",
                "description": "AI 규제",
            },
            {
                "source": "gov_msit",
                "target": "corp_samsung",
                "type": "REGULATES",
                "description": "AI 규제",
            },
            {
                "source": "cso_pam",
                "target": "gov_msit",
                "type": "OPPOSES",
                "description": "정부 정책 비판",
            },
            {
                "source": "cso_fair_ai",
                "target": "gov_msit",
                "type": "OPPOSES",
                "description": "설명가능성 의무화",
            },
            {
                "source": "journalist_kim_ys",
                "target": "gov_msit",
                "type": "REPORTS_ON",
                "description": "AI 정책 보도",
            },
            {
                "source": "corp_naver",
                "target": "alliance_kai",
                "type": "SUPPORTS",
                "description": "산업 연합 지지",
            },
        ],
    },
    ensure_ascii=False,
)


def _build_profile_response(agent_ids: list[str]) -> str:
    ideologies = [0.2, 0.35, 0.4, 0.5, 0.55, 0.6, 0.65, 0.7, 0.45, 0.3]
    profiles = []
    for i, aid in enumerate(agent_ids):
        profiles.append(
            {
                "agent_id": aid,
                "personality": "분석적이고 논리적인 성향",
                "speech_style": "격식체" if i % 2 == 0 else "구어체",
                "background": "AI 규제 관련 이해관계자",
                "ideology": ideologies[i % len(ideologies)],
                "topics": ["AI 규제", "기술 정책"],
                "sensitive_topics": ["개인정보"],
                "behavior_tendency": {
                    "post_rate": round(0.3 + (i % 5) * 0.1, 2),
                    "reply_rate": round(0.2 + (i % 4) * 0.1, 2),
                    "repost_rate": round(0.1 + (i % 3) * 0.1, 2),
                    "controversy_affinity": round(0.3 + (i % 5) * 0.1, 2),
                },
            }
        )
    return json.dumps(profiles, ensure_ascii=False)


class _MockLLM:
    """Dispatches realistic responses based on system prompt content."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str]] = []

    async def complete(self, *, system: str, user: str, model: str) -> str:
        self.calls.append((system, user, model))

        if "ontology design expert" in system.lower():
            return ONTOLOGY_RESP
        if "entity and relationship extraction" in system.lower():
            return EXTRACT_RESP
        if "프로필" in system or "에이전트" in system:
            agent_ids = re.findall(r"agent_id:\s*(\S+)", user)
            return _build_profile_response(agent_ids) if agent_ids else "[]"
        return "[]"


def _make_config(output_dir: Path) -> PipelineConfig:
    return PipelineConfig(
        input_path=SAMPLE_DOC,
        requirement="한국 AI 규제 정책을 둘러싼 이해관계자 간 소셜 미디어 토론 시뮬레이션",
        preset=Preset.QUICK,
        seed=42,
        output_dir=output_dir,
        model="mock-model",
    )


# ── Tests ───────────────────────────────────────────────────────────


async def test_quick_preset_pipeline_runs(tmp_path: Path) -> None:
    """Pipeline completes and produces the expected output structure."""
    ontology_a, ontology_b = await OntologyPipeline(_make_config(tmp_path), _MockLLM()).run()

    assert ontology_a.version == 1
    assert ontology_a.preset is Preset.QUICK
    assert ontology_a.agent_count == len(ontology_a.agents)
    assert ontology_a.agent_count >= 1
    assert len(ontology_b.stores) == ontology_a.agent_count


async def test_quick_preset_pydantic_roundtrip(tmp_path: Path) -> None:
    """Serialized JSON round-trips through Pydantic models."""
    ontology_a, ontology_b = await OntologyPipeline(_make_config(tmp_path), _MockLLM()).run()

    serializer = OntologySerializer()
    json_a = serializer.serialize_a(ontology_a)
    json_b = serializer.serialize_b(ontology_b)

    roundtrip_a = OntologyA.model_validate_json(json_a)
    roundtrip_b = OntologyB.model_validate_json(json_b)

    assert roundtrip_a.agent_count == ontology_a.agent_count
    assert set(roundtrip_a.agents) == set(ontology_a.agents)
    assert set(roundtrip_b.stores) == set(ontology_b.stores)


async def test_quick_preset_json_schema(tmp_path: Path) -> None:
    """Output passes JSON Schema (Draft 7) validation."""
    ontology_a, ontology_b = await OntologyPipeline(_make_config(tmp_path), _MockLLM()).run()

    serializer = OntologySerializer()
    data_a = json.loads(serializer.serialize_a(ontology_a))
    data_b = json.loads(serializer.serialize_b(ontology_b))

    assert serializer.validate_against_schema(data_a, "ontology_a") == []
    assert serializer.validate_against_schema(data_b, "ontology_b") == []


async def test_quick_preset_validator_passes(tmp_path: Path) -> None:
    """OntologyValidator consistency checks pass."""
    ontology_a, ontology_b = await OntologyPipeline(_make_config(tmp_path), _MockLLM()).run()

    result = OntologyValidator().validate(ontology_a, ontology_b)
    assert result.valid, f"errors: {result.errors}"
    assert result.errors == []


async def test_quick_preset_agent_fields(tmp_path: Path) -> None:
    """Every agent has required fields within valid ranges."""
    ontology_a, _ontology_b = await OntologyPipeline(_make_config(tmp_path), _MockLLM()).run()

    for agent_id, profile in ontology_a.agents.items():
        assert profile.skeleton, f"{agent_id} missing skeleton"
        assert 0.0 <= profile.ideology <= 1.0, f"{agent_id} ideology out of range"
        assert profile.topics, f"{agent_id} missing topics"
        bt = profile.behavior_tendency
        for field in ("post_rate", "reply_rate", "repost_rate", "controversy_affinity"):
            assert 0.0 <= getattr(bt, field) <= 1.0, f"{agent_id} {field} out of range"


async def test_quick_preset_generates_fixtures(tmp_path: Path) -> None:
    """Generate fixture files and validate they can be written."""
    ontology_a, ontology_b = await OntologyPipeline(_make_config(tmp_path), _MockLLM()).run()

    serializer = OntologySerializer()
    json_a = serializer.serialize_a(ontology_a)
    json_b = serializer.serialize_b(ontology_b)

    fixture_a = DATA_DIR / "sample_ontology_a.json"
    fixture_b = DATA_DIR / "sample_ontology_b.json"
    fixture_a.write_text(json_a, encoding="utf-8")
    fixture_b.write_text(json_b, encoding="utf-8")

    assert fixture_a.exists()
    assert fixture_b.exists()

    reloaded_a = OntologyA.model_validate_json(fixture_a.read_text(encoding="utf-8"))
    reloaded_b = OntologyB.model_validate_json(fixture_b.read_text(encoding="utf-8"))
    assert reloaded_a.agent_count == ontology_a.agent_count
    assert len(reloaded_b.stores) == len(ontology_b.stores)
