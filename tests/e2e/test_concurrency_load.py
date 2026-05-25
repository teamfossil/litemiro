"""100-agent OpenRouter load probe — manual run only.

CI excludes this via the ``manual`` marker. To run locally:

    OPENROUTER_API_KEY=... pytest -m manual tests/e2e/test_concurrency_load.py -s

Cost is roughly ~$0.03 per run with the default cheap model. The point
is to observe whether the Phase 2 default ``ConcurrencyController``
config (``semaphore_limit=10``, ``batch_size=20``, ``cooldown=0.5s``)
stays inside OpenRouter's rate limit at the simulation scale, and to
collect wall-clock data for later tuning.

``LiteLLMClient`` defaults to ``timeout_seconds=30``; pass a smaller
value to its ctor if a stalled provider would skew the throughput
number reported below.
"""

from __future__ import annotations

import os
import time

import pytest

from litemiro.core import ConcurrencyController
from litemiro.llm import LiteLLMClient

_REQUIRED_ENV = "OPENROUTER_API_KEY"
_MODEL = os.environ.get("LITEMIRO_LOAD_MODEL", "openrouter/openai/gpt-4o-mini")
_AGENT_COUNT = 100


@pytest.mark.manual
@pytest.mark.e2e
@pytest.mark.skipif(
    not os.environ.get(_REQUIRED_ENV),
    reason=f"requires {_REQUIRED_ENV} for live OpenRouter calls",
)
async def test_100_agent_default_config_against_openrouter(
    capsys: pytest.CaptureFixture[str],
) -> None:
    client = LiteLLMClient()
    controller = ConcurrencyController()

    async def call(agent_id: str) -> int:
        response = await client.complete(
            system="You are a brevity test. Answer with exactly one word.",
            user=f"ping-{agent_id}",
            model=_MODEL,
        )
        return len(response.content)

    started = time.perf_counter()
    items = tuple(f"a-{n:03d}" for n in range(_AGENT_COUNT))
    results = await controller.run_batched(items, call)
    elapsed = time.perf_counter() - started

    assert len(results) == _AGENT_COUNT
    # Observation-grade: a single empty completion shouldn't fail the probe.
    # Report the count alongside throughput; only fail if every call was empty.
    empty = sum(1 for r in results if r == 0)
    assert empty < _AGENT_COUNT, "OpenRouter returned empty content for every call"

    with capsys.disabled():
        print(
            f"\n[load] {_AGENT_COUNT} calls / {_MODEL} / "
            f"semaphore=10 batch=20 cooldown=0.5 → {elapsed:.2f}s "
            f"({_AGENT_COUNT / elapsed:.1f} req/s, empty={empty})",
        )
