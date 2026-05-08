from __future__ import annotations

import hashlib
import random

from litemiro.models import Agent


class AgentScheduler:
    def __init__(self, *, global_seed: int) -> None:
        self._global_seed = global_seed

    def select_active(self, agents: tuple[Agent, ...], round_num: int) -> tuple[str, ...]:
        if round_num < 0:
            raise ValueError(f"round_num must be >= 0, got {round_num}")
        if not agents:
            return ()
        rng = random.Random(self._derive_seed(round_num))
        return tuple(agent.agent_id for agent in agents if rng.random() < agent.activation_rate)

    def _derive_seed(self, round_num: int) -> int:
        digest = hashlib.sha256(f"{self._global_seed}:{round_num}".encode()).digest()
        return int.from_bytes(digest[:8], "big", signed=False)


__all__ = ["AgentScheduler"]
