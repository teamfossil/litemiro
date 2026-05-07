"""``AgentScheduler`` — owned by **A**.

Selects the active agents for a given round using each agent's own
``activation_rate`` field (``Agent.activation_rate``). The contract
pinned by the unit suite:

* Same ``(global_seed, round_num, agents)`` → same activation set.
  Across rounds the seed *varies* (different round_num → different
  RNG state) but is fully deterministic given the same inputs.
* Output preserves the input order — selection is a stable subset
  of the input tuple, never a reorder.
* ``activation_rate=0.0`` → that agent is *never* active.
  ``activation_rate=1.0`` → that agent is *always* active.

The per-round seed is derived from ``sha256(f"{global_seed}:{round_num}")``
truncated to 8 bytes — Python's ``random.Random`` accepts ``int`` reliably,
so we collapse the (seed, round) pair into a single integer rather than
relying on the deprecated tuple-seed path.
"""

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
