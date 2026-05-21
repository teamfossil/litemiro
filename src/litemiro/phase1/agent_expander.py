from __future__ import annotations

import random

from litemiro.phase1.local_graph import LocalGraph
from litemiro.phase1.models import AgentOrigin, AgentSeed, Entity


class AgentExpander:
    def __init__(self, graph: LocalGraph, requirement: str, seed: int = 42) -> None:
        self._graph = graph
        self._requirement = requirement
        self._rng = random.Random(seed)
        self._seq = 0

    def expand(self, core_seeds: list[AgentSeed], target_count: int) -> list[AgentSeed]:
        if len(core_seeds) >= target_count:
            return core_seeds[:target_count]

        self._seq = len(core_seeds)
        result = list(core_seeds)

        org_entities = [
            e
            for e in self._graph.entities.values()
            if e.type.lower() in ("organization", "org", "기관", "조직", "언론사", "기업")
        ]

        strategies = [
            lambda: self._generate_affiliated(org_entities),
            lambda: self._generate_public(self._requirement, 1),
            lambda: self._generate_opposition(result),
        ]
        strategy_idx = 0

        while len(result) < target_count:
            new_agents = strategies[strategy_idx % 3]()
            for agent in new_agents:
                if len(result) >= target_count:
                    break
                result.append(agent)
            strategy_idx += 1

        return result[:target_count]

    def _next_id(self) -> str:
        aid = f"agent_{self._seq:04d}"
        self._seq += 1
        return aid

    def _generate_affiliated(self, org_entities: list[Entity]) -> list[AgentSeed]:
        seeds: list[AgentSeed] = []
        for org in org_entities:
            count = self._rng.randint(3, 5)
            for _ in range(count):
                agent_id = self._next_id()
                context = (
                    f"소속 조직: {org.name} ({org.type})\n"
                    f"조직 요약: {org.summary}\n"
                    f"역할: 소속 구성원"
                )
                seeds.append(
                    AgentSeed(
                        agent_id=agent_id,
                        entity=None,
                        origin=AgentOrigin.DERIVED,
                        derived_from=org.id,
                        context=context,
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
            age = self._rng.choice(age_groups)
            region = self._rng.choice(regions)
            occupation = self._rng.choice(occupations)
            context = (
                f"layer: 일반시민\n"
                f"연령대: {age}\n"
                f"지역: {region}\n"
                f"직업: {occupation}\n"
                f"관심 주제: {requirement[:100]}"
            )
            seeds.append(
                AgentSeed(
                    agent_id=agent_id,
                    entity=None,
                    origin=AgentOrigin.DERIVED,
                    derived_from=None,
                    context=context,
                )
            )
        return seeds

    def _generate_opposition(self, existing_seeds: list[AgentSeed]) -> list[AgentSeed]:
        if not existing_seeds:
            return self._generate_public(self._requirement, 1)

        ideologies = [
            float(s.entity.attributes.get("ideology", 0.5))
            if s.entity and "ideology" in s.entity.attributes
            else 0.5
            for s in existing_seeds
        ]
        avg_ideology = sum(ideologies) / len(ideologies) if ideologies else 0.5

        seeds: list[AgentSeed] = []
        agent_id = self._next_id()

        if avg_ideology < 0.5:
            counter_ideology = round(self._rng.uniform(0.6, 0.9), 2)
            stance = "보수적"
        else:
            counter_ideology = round(self._rng.uniform(0.1, 0.4), 2)
            stance = "진보적"

        context = (
            f"layer: 일반시민\n"
            f"이념 성향: {stance} (ideology={counter_ideology})\n"
            f"관심 주제: {self._requirement[:100]}"
        )
        seeds.append(
            AgentSeed(
                agent_id=agent_id,
                entity=None,
                origin=AgentOrigin.DERIVED,
                derived_from=None,
                context=context,
            )
        )
        return seeds
