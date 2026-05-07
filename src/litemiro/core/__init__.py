"""Phase 2 engine core — owned by **A** (권현재).

Components:
- ``StateStore``: 시뮬레이션 상태 보관 + 체크포인트 IO
- ``AgentScheduler``: 라운드별 활성 에이전트 선정
- ``ConcurrencyController``: asyncio Semaphore + 배치 + cooldown
- ``RoundManager``: 라운드 루프 오케스트레이션 (STEP 7 — C 합의 후)

Test-only fakes for owner-A surface live in ``tests/fakes.py``.
"""

from __future__ import annotations

from litemiro.core._types import RoundOutcome
from litemiro.core.agent_scheduler import AgentScheduler
from litemiro.core.concurrency_controller import ConcurrencyController
from litemiro.core.context_builder import build_context
from litemiro.core.state_store import StateStore

__all__ = [
    "AgentScheduler",
    "ConcurrencyController",
    "RoundOutcome",
    "StateStore",
    "build_context",
]
