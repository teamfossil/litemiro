from __future__ import annotations

import random

from litemiro.phase1.actor_classifier import infer_persona_mode
from litemiro.phase1.local_graph import LocalGraph
from litemiro.phase1.models import (
    STANCE_QUOTA,
    AgentOrigin,
    AgentSeed,
    Entity,
    PersonaMode,
    stance_bucket,
)


class AgentExpander:
    def __init__(self, graph: LocalGraph, requirement: str, seed: int = 42) -> None:
        self._graph = graph
        self._requirement = requirement
        self._rng = random.Random(seed)
        self._seq = 0
        self._stance_targets: list[float] = []

    def expand(self, core_seeds: list[AgentSeed], target_count: int) -> list[AgentSeed]:
        if len(core_seeds) >= target_count:
            return core_seeds[:target_count]

        self._seq = len(core_seeds)
        result = list(core_seeds)
        self._stance_targets = _allocate_stance_targets(target_count - len(core_seeds))
        self._rng.shuffle(self._stance_targets)

        org_entities = [
            entity
            for entity in self._graph.entities.values()
            if infer_persona_mode(
                type_name=entity.type,
                name=entity.name,
                attributes=entity.attributes,
                summary=entity.summary,
            )
            == PersonaMode.REPRESENTATIVE
        ]

        strategies = [
            lambda remaining: self._generate_affiliated(org_entities, remaining),
            lambda remaining: self._generate_public(self._requirement, min(1, remaining)),
            lambda remaining: self._generate_public(self._requirement, min(1, remaining)),
        ]
        strategy_idx = 0

        while len(result) < target_count:
            new_agents = strategies[strategy_idx % 3](target_count - len(result))
            for agent in new_agents:
                result.append(agent)
            strategy_idx += 1

        return result[:target_count]

    def _next_id(self) -> str:
        aid = f"agent_{self._seq:04d}"
        self._seq += 1
        return aid

    def _next_stance_target(self) -> float:
        if not self._stance_targets:
            raise RuntimeError("derived stance quota exhausted")
        return self._stance_targets.pop()

    def _generate_affiliated(self, org_entities: list[Entity], limit: int) -> list[AgentSeed]:
        seeds: list[AgentSeed] = []
        for org in org_entities:
            count = self._rng.randint(3, 5)
            for _ in range(count):
                if len(seeds) >= limit:
                    return seeds
                agent_id = self._next_id()
                stance_target = self._next_stance_target()
                context = (
                    f"소속 조직: {org.name} ({org.type})\n"
                    f"조직 요약: {org.summary}\n"
                    f"역할: 소속 구성원\n"
                    f"{_stance_context(stance_target)}"
                )
                seeds.append(
                    AgentSeed(
                        agent_id=agent_id,
                        entity=None,
                        origin=AgentOrigin.DERIVED,
                        derived_from=org.id,
                        context=context,
                        stance_target=stance_target,
                    )
                )
        return seeds

    def _generate_public(self, requirement: str, count: int) -> list[AgentSeed]:
        age_groups = ["10대", "20대", "30대", "40대", "50대", "60대 이상"]
        regions = [
            "서울",
            "경기",
            "부산",
            "대구",
            "인천",
            "광주",
            "대전",
            "울산",
            "경상",
            "전라",
            "충청",
            "강원",
            "제주",
        ]
        occupations = [
            "직장인",
            "학생",
            "자영업자",
            "주부",
            "프리랜서",
            "공무원",
            "교사",
            "연구원",
            "의료인",
            "농업인",
        ]

        seeds: list[AgentSeed] = []
        for _ in range(count):
            agent_id = self._next_id()
            stance_target = self._next_stance_target()
            age = self._rng.choice(age_groups)
            region = self._rng.choice(regions)
            occupation = self._rng.choice(occupations)
            context = (
                f"layer: 일반시민\n"
                f"연령대: {age}\n"
                f"지역: {region}\n"
                f"직업: {occupation}\n"
                f"관심 주제: {requirement[:100]}\n"
                f"{_stance_context(stance_target)}"
            )
            seeds.append(
                AgentSeed(
                    agent_id=agent_id,
                    entity=None,
                    origin=AgentOrigin.DERIVED,
                    derived_from=None,
                    context=context,
                    stance_target=stance_target,
                )
            )
        return seeds


def _allocate_stance_targets(count: int) -> list[float]:
    raw_counts = [count * ratio for _bucket, ratio, _target in STANCE_QUOTA]
    counts = [int(raw) for raw in raw_counts]
    remaining = count - sum(counts)
    by_largest_remainder = sorted(
        range(len(STANCE_QUOTA)),
        key=lambda index: raw_counts[index] - counts[index],
        reverse=True,
    )
    for index in by_largest_remainder[:remaining]:
        counts[index] += 1

    targets: list[float] = []
    for count_for_bucket, (_bucket, _ratio, target) in zip(counts, STANCE_QUOTA, strict=True):
        targets.extend([target] * count_for_bucket)
    return targets


def _stance_context(target: float) -> str:
    labels = {
        "critical": "비판적",
        "neutral": "중립적",
        "supportive": "우호적",
    }
    return f"현재 토론 주제 태도: {labels[stance_bucket(target)]} (stance_target={target:.1f})"
