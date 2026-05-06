"""Prompt templates for ``ActionSelector``.

The Phase 2 design doc only sketched a four-part recipe ("persona +
memory + feed + recent action"). B pins the exact text and ordering
here so the test suite can assert composition (and so prompt churn
stays diff-reviewable instead of buried inside ``selector.py``).

The system prompt declares the *vocabulary* (every ``ActionType`` and
its required fields) and the strict JSON-only output format. The user
prompt is the per-round payload (feed + recent + counts + round). Both
are pure functions of the inputs so they are deterministic across runs.
"""

from __future__ import annotations

import json

from litemiro.models import ActionContext, ActionType

_SYSTEM_HEADER = (
    "You are agent {agent_id}, one participant in a small social-network "
    "simulation. Each round you receive a snapshot of your feed and your "
    "recent actions, and you must respond with EXACTLY one JSON object "
    "describing your next action."
)

_SYSTEM_SCHEMA = (
    "Output schema (one JSON object, no prose, no code fences):\n"
    '  {{"type": <ActionType>, "target_post_id": <str|null>, '
    '"target_agent_id": <str|null>, "content": <str|null>}}\n'
    "Allowed ActionType values: {action_types}.\n"
    "Field rules:\n"
    "  - CREATE_POST  → non-empty content; no targets.\n"
    "  - LIKE_POST    → target_post_id from your feed.\n"
    "  - REPOST       → target_post_id from your feed.\n"
    "  - QUOTE_POST   → target_post_id from your feed AND non-empty content.\n"
    "  - FOLLOW       → target_agent_id of an author visible in your feed.\n"
    "  - DO_NOTHING   → omit all targets and content."
)


def compose_system(agent_id: str, context: ActionContext) -> str:
    """Build the system prompt — the persona card + output schema.

    The persona card is verbatim JSON of the agent's identity-shaped
    fields (``interests``, ``persona_traits``, ``memory_summary``) so a
    well-instructed LLM can read it without parsing prose.
    """
    persona_card = json.dumps(
        {
            "agent_id": context.agent.agent_id,
            "interests": list(context.agent.interests),
            "persona_traits": dict(context.agent.persona_traits),
            "memory_summary": context.agent.memory_summary,
        },
        ensure_ascii=False,
        indent=2,
    )
    action_types = ", ".join(at.value for at in ActionType)
    return "\n\n".join(
        [
            _SYSTEM_HEADER.format(agent_id=agent_id),
            "Persona card:\n" + persona_card,
            _SYSTEM_SCHEMA.format(action_types=action_types),
            "Respond with the JSON object only.",
        ]
    )


def _feed_block(context: ActionContext) -> str:
    if not context.feed:
        return "Your feed is empty this round."
    lines = ["Your feed (most relevant first):"]
    for post in context.feed:
        snippet = post.content[:120]
        lines.append(f"  - [{post.post_id}] @{post.author_id}: {snippet}")
    return "\n".join(lines)


def _recent_block(context: ActionContext) -> str:
    if not context.recent_actions:
        return "You have taken no recent actions."
    lines = ["Your recent actions:"]
    for action in context.recent_actions:
        parts = [action.type.value]
        if action.target_post_id is not None:
            parts.append(f"target_post_id={action.target_post_id}")
        if action.target_agent_id is not None:
            parts.append(f"target_agent_id={action.target_agent_id}")
        if action.content is not None:
            parts.append(f"content={action.content[:60]}")
        lines.append("  - " + " ".join(parts))
    return "\n".join(lines)


def compose_user(context: ActionContext) -> str:
    """Build the per-round user prompt."""
    return "\n\n".join(
        [
            f"Round: {context.round_num}",
            f"Followers: {context.follower_count}, Following: {context.following_count}",
            _feed_block(context),
            _recent_block(context),
            "Choose your next action.",
        ]
    )


__all__ = ["compose_system", "compose_user"]
