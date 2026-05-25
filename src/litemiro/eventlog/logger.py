"""``EventLogger`` — the Phase 2 → Phase 3 JSONL sink.

Pinned by the unit suite:

* **Append-only**: ``RoundEvent`` already carries its own monotonic
  ``round_num``/``timestamp``, so the sink never rewrites or reorders.
  Constructing an ``EventLogger`` against an existing file appends.
* **Concurrency**: guarded by ``asyncio.Lock`` so two coroutines that
  ``await log_event`` in the same round produce two whole, non-
  interleaved lines.
* **Durability**: every line is flushed before ``log_event`` returns
  so a mid-round crash loses at most the in-flight event, and the
  written JSONL stays partial-but-valid (every persisted line is a
  complete JSON object terminated by ``\\n``).
* **Lifecycle**: ``aclose`` is idempotent so the round runner can put
  it in a ``finally`` block without tracking "did I close already".
  Logging after ``aclose`` raises ``RuntimeError`` — silently dropping
  events would mask a wiring bug.

Serialisation lives on ``RoundEvent.to_jsonl`` (sorted keys, no ASCII
escape, ``exclude_none``) — the sink only worries about ordering,
durability, and concurrency. The Protocol surface is
``EventLoggerLike`` in ``litemiro.interfaces``.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import IO, TYPE_CHECKING

if TYPE_CHECKING:
    from litemiro.models import RoundEvent


class EventLogger:
    def __init__(self, path: Path) -> None:
        self._path = path
        # ``Path("run.jsonl").parent`` is ``Path(".")`` which ``mkdir``
        # treats as a no-op, so callers can pass a bare filename for
        # ad-hoc runs without first guaranteeing a parent exists.
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # ``newline=""`` disables Python's universal-newline rewriting
        # so the written byte after each event is always ``\\n``,
        # matching the JSONL convention regardless of platform.
        self._handle: IO[str] | None = open(  # noqa: SIM115 — closed in aclose
            self._path, "a", encoding="utf-8", newline=""
        )
        self._lock = asyncio.Lock()

    async def log_event(self, event: RoundEvent) -> None:
        async with self._lock:
            handle = self._handle
            if handle is None:
                raise RuntimeError(f"EventLogger already closed: {self._path}")
            handle.write(event.to_jsonl())
            handle.write("\n")
            handle.flush()

    async def aclose(self) -> None:
        async with self._lock:
            if self._handle is None:
                return
            self._handle.close()
            self._handle = None


__all__ = ["EventLogger"]
