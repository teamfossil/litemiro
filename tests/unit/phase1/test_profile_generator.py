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
