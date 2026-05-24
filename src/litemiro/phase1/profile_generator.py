from __future__ import annotations

import asyncio
import json
import logging
from typing import cast

from json_repair import repair_json
from tenacity import retry, stop_after_attempt, wait_exponential

from litemiro.phase1.llm import Phase1LLMClient, response_text
from litemiro.phase1.models import AgentProfile, AgentSeed, BehaviorTendency

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = (
    "소셜 미디어 시뮬레이션용 에이전트 프로필을 생성합니다. "
    "각 에이전트의 엔티티 정보와 문맥을 바탕으로 프로필을 작성하세요."
)

_BATCH_SIZE = 10

_ENTITY_TYPE_DEFAULTS: dict[str, dict[str, object]] = {
    "person": {"personality": "분석적이고 논리적인 성향", "speech_style": "격식체"},
    "organization": {"personality": "조직의 공식 입장을 대변함", "speech_style": "공식 문체"},
    "politician": {"personality": "설득력 있고 전략적", "speech_style": "공식 연설체"},
    "journalist": {"personality": "객관적이고 탐구적", "speech_style": "보도체"},
}


class ProfileGenerator:
    def __init__(
        self,
        llm: Phase1LLMClient,
        model: str = "openrouter/qwen/qwen-plus",
        max_concurrency: int = 5,
    ) -> None:
        self._llm = llm
        self._model = model
        self._semaphore = asyncio.Semaphore(max_concurrency)

    async def generate(
        self, seeds: list[AgentSeed], simulation_requirement: str
    ) -> list[AgentProfile]:
        batches = [seeds[i : i + _BATCH_SIZE] for i in range(0, len(seeds), _BATCH_SIZE)]
        tasks = [self._generate_batch(batch, simulation_requirement) for batch in batches]
        results = await asyncio.gather(*tasks)
        return [profile for batch_result in results for profile in batch_result]

    async def _generate_batch(
        self, batch: list[AgentSeed], simulation_requirement: str
    ) -> list[AgentProfile]:
        async with self._semaphore:
            agent_lines: list[str] = []
            for seed in batch:
                entity_info = ""
                if seed.entity:
                    entity_info = (
                        f"  엔티티명: {seed.entity.name}\n"
                        f"  엔티티유형: {seed.entity.type}\n"
                        f"  요약: {seed.entity.summary}"
                    )
                else:
                    entity_info = "  엔티티: 없음 (파생 에이전트)"
                agent_lines.append(
                    f"agent_id: {seed.agent_id}\n{entity_info}\n  문맥: {seed.context}"
                )

            user_prompt = (
                f"시뮬레이션 요구사항:\n{simulation_requirement}\n\n"
                f"다음 에이전트들에 대한 프로필을 JSON 배열로 생성하세요.\n"
                "각 항목은 반드시 agent_id, personality, speech_style, background, "
                "ideology (0.0~1.0), topics (list[str]), sensitive_topics (list[str]), "
                "behavior_tendency (post_rate, reply_rate, repost_rate, "
                "controversy_affinity) 포함.\n\n"
                + "\n\n".join(agent_lines)
                + "\n\ntemperature: 0.5\n출력: JSON 배열만, 마크다운 없이."
            )

            try:
                profiles = await self._call_with_retry(user_prompt)
            except Exception:
                logger.warning("LLM batch failed for %d seeds, using fallback", len(batch))
                return [self._build_fallback_profile(seed) for seed in batch]

            seed_map = {s.agent_id: s for s in batch}
            result: list[AgentProfile] = []
            for item in profiles:
                agent_id_raw = item.get("agent_id")
                agent_id = agent_id_raw if isinstance(agent_id_raw, str) else None
                profile_seed = seed_map.get(agent_id) if agent_id else None
                if profile_seed is None:
                    continue
                try:
                    result.append(_parse_profile(item, profile_seed))
                except Exception:
                    logger.warning("Profile parse failed for %s, using fallback", agent_id)
                    result.append(self._build_fallback_profile(profile_seed))

            # fill any seeds that didn't get a profile
            returned_ids = {p.agent_id for p in result}
            for seed in batch:
                if seed.agent_id not in returned_ids:
                    result.append(self._build_fallback_profile(seed))

            return result

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    async def _call_with_retry(self, user_prompt: str) -> list[dict[str, object]]:
        response = await self._llm.complete(
            system=_SYSTEM_PROMPT,
            user=user_prompt,
            model=self._model,
        )
        raw = response_text(response)
        repaired = repair_json(raw)
        data = json.loads(repaired)
        if isinstance(data, list):
            return cast(list[dict[str, object]], data)
        if isinstance(data, dict):
            return [cast(dict[str, object], data)]
        return []

    def _build_fallback_profile(self, seed: AgentSeed) -> AgentProfile:
        entity_type = seed.entity.type if seed.entity else "citizen"
        defaults = _ENTITY_TYPE_DEFAULTS.get(entity_type.lower(), {})
        entity_name = seed.entity.name if seed.entity else f"시민_{seed.agent_id}"
        return AgentProfile(
            agent_id=seed.agent_id,
            name=entity_name,
            entity_type=entity_type,
            origin=seed.origin,
            derived_from=seed.derived_from,
            skeleton=_build_skeleton(seed),
            ideology=0.5,
            topics=_fallback_topics(seed),
            sensitive_topics=[],
            personality=str(defaults.get("personality", "일반적인 소셜 미디어 사용자")),
            speech_style=str(defaults.get("speech_style", "구어체")),
            background=seed.context[:200] if seed.context else "",
            behavior_tendency=BehaviorTendency(),
        )


def _parse_profile(item: dict[str, object], seed: AgentSeed) -> AgentProfile:
    bt_raw = item.get("behavior_tendency", {})
    if not isinstance(bt_raw, dict):
        bt_raw = {}
    behavior_tendency = BehaviorTendency(
        post_rate=_float_value(bt_raw.get("post_rate"), 0.5),
        reply_rate=_float_value(bt_raw.get("reply_rate"), 0.3),
        repost_rate=_float_value(bt_raw.get("repost_rate"), 0.2),
        controversy_affinity=_float_value(bt_raw.get("controversy_affinity"), 0.5),
    )
    entity_name = seed.entity.name if seed.entity else f"시민_{seed.agent_id}"
    entity_type = seed.entity.type if seed.entity else "citizen"

    topics = item.get("topics", [])
    if not isinstance(topics, list):
        topics = []
    if not topics:
        topics = _fallback_topics(seed)
    sensitive_topics = item.get("sensitive_topics", [])
    if not isinstance(sensitive_topics, list):
        sensitive_topics = []

    return AgentProfile(
        agent_id=seed.agent_id,
        name=str(item.get("name", entity_name)),
        entity_type=entity_type,
        origin=seed.origin,
        derived_from=seed.derived_from,
        skeleton=_build_skeleton(seed),
        ideology=_float_value(item.get("ideology"), 0.5),
        topics=[str(t) for t in topics],
        sensitive_topics=[str(t) for t in sensitive_topics],
        personality=str(item.get("personality", "")),
        speech_style=str(item.get("speech_style", "")),
        background=str(item.get("background", "")),
        behavior_tendency=behavior_tendency,
    )


def _build_skeleton(seed: AgentSeed) -> dict[str, object]:
    entity = seed.entity
    skeleton: dict[str, object] = {
        "origin": seed.origin.value,
        "layer": entity.type if entity else "derived",
        "entity_type": entity.type if entity else "citizen",
        "name": entity.name if entity else f"시민_{seed.agent_id}",
    }
    if entity:
        skeleton["source_entity_id"] = entity.id
        if entity.attributes:
            skeleton["attributes"] = dict(entity.attributes)
    if seed.derived_from:
        skeleton["derived_from"] = seed.derived_from
    return skeleton


def _fallback_topics(seed: AgentSeed) -> list[str]:
    if seed.entity:
        topics = [seed.entity.type]
        if seed.entity.name:
            topics.append(seed.entity.name)
        return topics[:2]
    if seed.derived_from:
        return [seed.derived_from]
    return ["general"]


def _float_value(value: object, default: float) -> float:
    if not isinstance(value, int | float | str):
        return default
    try:
        return float(value)
    except ValueError:
        return default
