"""``StateStore`` — owned by **A**.

Holds the live simulation state (agents, posts, the social graph) and
manages JSON checkpoints. The contract pinned by the unit suite:

* ``StateStoreLike`` Protocol satisfied — ``get_*`` raise ``KeyError``
  for unknown ids; ``add_post`` raises ``ValueError`` for duplicates
  (matches B's ``FeedEngine.index_post`` style — D10).
* ``get_random_seed`` mirrors phase-2-B's ``InMemoryStateStore``:
  ``int.from_bytes(sha256(f"{global_seed}:{agent_id}").digest()[:8], "big")``
  — deterministic, no mutating RNG state on the Protocol.
* ``save_checkpoint(N)`` is idempotent — calling it twice with the same
  round number produces a byte-identical file. Serialisation sorts dict
  keys / list members and uses compact separators so a fresh restore
  produces the same in-memory ordering.
* ``restore_checkpoint(N)`` round-trips every getter back to the snapshot
  state, including the social graph.
* ``_prune_old_checkpoints(keep=3)`` retains the three most recent
  ``checkpoint_round_*.json`` files; older ones are deleted.

The social graph is held as a ``SocialGraphLike``. Serialisation goes
through ``social.to_dict()`` (Protocol-level). Restoration cannot live
on the Protocol (``from_dict`` would have to be a classmethod), so the
caller — typically the composition root or a unit test — supplies a
``social_factory`` that produces a fresh ``SocialGraphLike`` from a
``Mapping[str, Iterable[str]]``. This keeps ``StateStore`` Protocol-only
on the read path while letting tests inject ``FakeSocialGraph.from_dict``
and production inject ``litemiro.social.SocialGraph.from_dict``.

Disk IO is wrapped in ``asyncio.to_thread`` so the event loop is never
blocked on JSON serialisation, without pulling in ``aiofiles``.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol

from litemiro.models import Agent, Post

if TYPE_CHECKING:
    from litemiro.interfaces import SocialGraphLike


class _SocialGraphFactory(Protocol):
    """Callable that rebuilds a ``SocialGraphLike`` from a serialised dict."""

    def __call__(self, data: Mapping[str, Iterable[str]]) -> SocialGraphLike: ...


_CHECKPOINT_FILENAME = re.compile(r"^checkpoint_round_(\d+)\.json$")


class StateStore:
    def __init__(
        self,
        *,
        agents: Iterable[Agent],
        social: SocialGraphLike,
        social_factory: _SocialGraphFactory,
        checkpoint_dir: Path,
        global_seed: int,
    ) -> None:
        self._agents: dict[str, Agent] = {a.agent_id: a for a in agents}
        self._posts: dict[str, Post] = {}
        self._social: SocialGraphLike = social
        self._social_factory = social_factory
        self._checkpoint_dir = Path(checkpoint_dir)
        self._checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self._global_seed = global_seed

    # ------------------------------------------------------------------
    # StateStoreLike Protocol surface
    # ------------------------------------------------------------------

    def get_agent(self, agent_id: str) -> Agent:
        try:
            return self._agents[agent_id]
        except KeyError as exc:
            raise KeyError(f"unknown agent_id: {agent_id}") from exc

    def list_agent_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._agents))

    def get_post(self, post_id: str) -> Post:
        try:
            return self._posts[post_id]
        except KeyError as exc:
            raise KeyError(f"unknown post_id: {post_id}") from exc

    def list_posts(self) -> tuple[Post, ...]:
        return tuple(self._posts[pid] for pid in sorted(self._posts))

    def add_post(self, post: Post) -> None:
        if post.post_id in self._posts:
            raise ValueError(f"post already exists: {post.post_id}")
        self._posts[post.post_id] = post

    def replace_post(self, post: Post) -> None:
        if post.post_id not in self._posts:
            raise KeyError(f"unknown post_id: {post.post_id}")
        self._posts[post.post_id] = post

    def get_random_seed(self, agent_id: str) -> int:
        digest = hashlib.sha256(
            f"{self._global_seed}:{agent_id}".encode()
        ).digest()
        return int.from_bytes(digest[:8], "big", signed=False)

    # ------------------------------------------------------------------
    # Read accessors that aren't on the Protocol but composition needs
    # ------------------------------------------------------------------

    @property
    def social(self) -> SocialGraphLike:
        return self._social

    @property
    def checkpoint_dir(self) -> Path:
        return self._checkpoint_dir

    # ------------------------------------------------------------------
    # Checkpoint IO
    # ------------------------------------------------------------------

    async def save_checkpoint(self, round_num: int) -> Path:
        if round_num < 0:
            raise ValueError(f"round_num must be >= 0, got {round_num}")
        payload = self._serialize_to_dict()
        text = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        path = self._checkpoint_path(round_num)
        await asyncio.to_thread(path.write_text, text, encoding="utf-8")
        self._prune_old_checkpoints(keep=3)
        return path

    async def restore_checkpoint(self, round_num: int) -> None:
        path = self._checkpoint_path(round_num)
        text = await asyncio.to_thread(path.read_text, encoding="utf-8")
        payload = json.loads(text)
        self._deserialize_from_dict(payload)

    def latest_checkpoint_round(self) -> int | None:
        rounds = self._existing_rounds()
        return max(rounds) if rounds else None

    def _prune_old_checkpoints(self, *, keep: int = 3) -> None:
        if keep < 1:
            raise ValueError(f"keep must be >= 1, got {keep}")
        rounds = sorted(self._existing_rounds(), reverse=True)
        for stale in rounds[keep:]:
            self._checkpoint_path(stale).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _checkpoint_path(self, round_num: int) -> Path:
        return self._checkpoint_dir / f"checkpoint_round_{round_num:04d}.json"

    def _existing_rounds(self) -> list[int]:
        if not self._checkpoint_dir.exists():
            return []
        rounds: list[int] = []
        for entry in self._checkpoint_dir.iterdir():
            match = _CHECKPOINT_FILENAME.match(entry.name)
            if match is not None:
                rounds.append(int(match.group(1)))
        return rounds

    def _serialize_to_dict(self) -> dict[str, Any]:
        # `agents` and `posts` are sorted by id so the dict is canonical;
        # `social` uses the Protocol's `to_dict` (already sorted by B's
        # `SocialGraph` and our `FakeSocialGraph`).
        return {
            "agents": {
                aid: self._agents[aid].model_dump(mode="json")
                for aid in sorted(self._agents)
            },
            "posts": {
                pid: self._posts[pid].model_dump(mode="json")
                for pid in sorted(self._posts)
            },
            "social": dict(self._social.to_dict()),
            "global_seed": self._global_seed,
        }

    def _deserialize_from_dict(self, payload: Mapping[str, Any]) -> None:
        # `Agent` / `Post` are strict-mode by default so engine-internal
        # builds catch type drift. Checkpoint restore is the one legitimate
        # rehydration path where strict needs to relax — JSON has no tuple,
        # so `interests` / `topics` arrive as `list[str]` and must coerce.
        self._agents = {
            aid: Agent.model_validate(data, strict=False)
            for aid, data in payload.get("agents", {}).items()
        }
        self._posts = {
            pid: Post.model_validate(data, strict=False)
            for pid, data in payload.get("posts", {}).items()
        }
        self._social = self._social_factory(payload.get("social", {}))
        # `global_seed` is treated as immutable from construction — restore
        # is informational only. Mismatched seeds would silently change
        # `get_random_seed` outputs, so we surface it as an error rather
        # than letting a copy-pasted checkpoint pollute determinism.
        recorded = payload.get("global_seed")
        if recorded is not None and recorded != self._global_seed:
            raise ValueError(
                f"global_seed mismatch on restore: "
                f"checkpoint={recorded!r}, store={self._global_seed!r}"
            )


__all__ = ["StateStore"]
