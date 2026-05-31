"""ProfileGenerator unit tests."""

from __future__ import annotations

import json
from collections.abc import Callable

import pytest

from litemiro.phase1.llm import Phase1LLMClient
from litemiro.phase1.models import AgentOrigin, AgentSeed, Entity
from litemiro.phase1.profile_generator import ProfileGenerator

VALID_PROFILE_RESPONSE = json.dumps(
    [
        {
            "agent_id": "agent_0001",
            "personality": "날카로운 분석력과 비판적 시각",
            "speech_style": "~다 체, 통계 인용",
            "background": "한겨레 입사 10년차 정치부 기자",
            "ideology": 0.3,
            "topics": ["정치", "경제"],
            "sensitive_topics": ["부동산"],
            "behavior_tendency": {
                "post_rate": 0.6,
                "reply_rate": 0.4,
                "repost_rate": 0.3,
                "follow_rate": 0.35,
                "controversy_affinity": 0.7,
            },
        },
        {
            "agent_id": "agent_0002",
            "personality": "신중하고 원칙적인 성격",
            "speech_style": "공식적 어투",
            "background": "개인정보보호위원회 위원장",
            "ideology": 0.4,
            "topics": ["개인정보", "규제"],
            "sensitive_topics": [],
            "behavior_tendency": {
                "post_rate": 0.3,
                "reply_rate": 0.2,
                "repost_rate": 0.1,
                "follow_rate": 0.15,
                "controversy_affinity": 0.4,
            },
        },
    ]
)


@pytest.mark.asyncio
async def test_generate_profiles(
    fake_llm: Callable[..., Phase1LLMClient],
    sample_agent_seeds: list[AgentSeed],
) -> None:
    llm = fake_llm(VALID_PROFILE_RESPONSE)
    gen = ProfileGenerator(llm=llm, model="test")
    profiles = await gen.generate(sample_agent_seeds, "AI 규제 시뮬레이션")
    assert len(profiles) == 2
    assert profiles[0].agent_id == "agent_0001"
    assert profiles[0].ideology == 0.3
    assert profiles[0].personality == "날카로운 분석력과 비판적 시각"
    assert profiles[0].skeleton["source_entity_id"] == "journalist_kim"
    assert profiles[0].topics == ["정치", "경제"]
    assert profiles[0].behavior_tendency.follow_rate == 0.35
    assert profiles[1].behavior_tendency.follow_rate == 0.15
    # 정상 경로는 fallback 카운트 0 — #109 silent fallback 가시화 후
    # 정상 케이스의 노이즈가 안 생기는지 확인.
    assert gen.fallback_count == 0


@pytest.mark.asyncio
async def test_generate_empty_seeds(fake_llm: Callable[..., Phase1LLMClient]) -> None:
    llm = fake_llm()
    gen = ProfileGenerator(llm=llm, model="test")
    profiles = await gen.generate([], "req")
    assert profiles == []


@pytest.mark.asyncio
async def test_fallback_on_bad_response(fake_llm: Callable[..., Phase1LLMClient]) -> None:
    seeds = [
        AgentSeed(
            agent_id="agent_0001",
            entity=Entity(id="e1", type="Journalist", name="김기자"),
            origin=AgentOrigin.EXTRACTED,
        ),
    ]
    llm = fake_llm("not valid json at all {{{", "still bad", "nope")
    gen = ProfileGenerator(llm=llm, model="test")
    profiles = await gen.generate(seeds, "req")
    assert len(profiles) == 1
    assert profiles[0].agent_id == "agent_0001"
    assert profiles[0].ideology == 0.5  # fallback default
    assert profiles[0].skeleton["source_entity_id"] == "e1"
    assert profiles[0].topics == ["Journalist", "김기자"]
    # #109: retry exhaust 로 배치 전체 fallback → seed 수만큼 카운트.
    assert gen.fallback_count == 1


@pytest.mark.asyncio
async def test_fallback_count_includes_missing_agent_ids(
    fake_llm: Callable[..., Phase1LLMClient],
    sample_agent_seeds: list[AgentSeed],
) -> None:
    """LLM 이 일부 seed 의 agent_id 만 반환하면 누락 seed 가 fallback 으로 떨어지고
    그 수가 카운트에 잡혀야 한다. 정상 응답 1건 + 누락 1건 → fallback_count == 1.
    """
    partial = json.dumps(
        [
            {
                "agent_id": "agent_0001",
                "personality": "분석적",
                "speech_style": "보도체",
                "background": "정치부 기자",
                "ideology": 0.3,
                "topics": ["정치"],
                "sensitive_topics": [],
                "behavior_tendency": {},
            }
        ]
    )
    llm = fake_llm(partial)
    gen = ProfileGenerator(llm=llm, model="test")
    profiles = await gen.generate(sample_agent_seeds, "req")
    assert len(profiles) == 2
    assert gen.fallback_count == 1


@pytest.mark.asyncio
async def test_empty_profile_topics_fall_back(
    fake_llm: Callable[..., Phase1LLMClient],
    sample_agent_seeds: list[AgentSeed],
) -> None:
    llm = fake_llm(
        json.dumps(
            [
                {
                    "agent_id": "agent_0001",
                    "personality": "신중함",
                    "speech_style": "분석체",
                    "background": "정치부 기자",
                    "ideology": 0.4,
                    "topics": [],
                    "sensitive_topics": [],
                    "behavior_tendency": {},
                }
            ]
        )
    )
    gen = ProfileGenerator(llm=llm, model="test")
    profiles = await gen.generate(sample_agent_seeds[:1], "AI 규제 시뮬레이션")
    assert profiles[0].topics == ["Journalist", "김영수"]
    # behavior_tendency 가 빈 객체일 때 모든 rate 가 default 로 채워진다 —
    # follow_rate / like_rate 가 누락되어도 Phase 2 ActionSelector 가 신호를 받을 수 있게.
    assert profiles[0].behavior_tendency.follow_rate == 0.2
    assert profiles[0].behavior_tendency.like_rate == 0.4


@pytest.mark.asyncio
async def test_generate_reraises_content_filter(
    monkeypatch: pytest.MonkeyPatch,
    sample_agent_seeds: list[AgentSeed],
) -> None:
    """content filter 는 fallback profile 로 덮지 않고 전파한다 (#126) — fallback chain 이
    다른 모델로 step4 를 재시도할 수 있어야 하기 때문. 비-filter 실패는 기존대로 fallback."""

    class _DummyLLM:
        async def complete(self, *, system: str, user: str, model: str) -> str:
            return ""

    async def _filter_retry(self: ProfileGenerator, user_prompt: str) -> list[dict[str, object]]:
        raise RuntimeError("litellm.BadRequestError: data_inspection_failed")

    monkeypatch.setattr(ProfileGenerator, "_call_with_retry", _filter_retry)
    gen = ProfileGenerator(llm=_DummyLLM(), model="test")
    with pytest.raises(RuntimeError, match="data_inspection_failed"):
        await gen.generate(sample_agent_seeds[:1], "AI 규제 시뮬레이션")
