"""Prompt templates for ``ActionSelector``.

The Phase 2 design doc only sketched a four-part recipe ("persona +
memory + feed + recent action"). B pins the exact text and ordering
here so the test suite can assert composition (and so prompt churn
stays diff-reviewable instead of buried inside ``selector.py``).

The system prompt declares the *vocabulary* (every ``ActionType`` and
its required fields) and the strict JSON-only output format. The user
prompt is the per-round payload (feed + recent + counts + round). Both
are pure functions of the inputs so they are deterministic across runs.

Phase 1 (dual-ontology) freezes the persona key set: ``agent_id, name,
entity_type, personality, speech_style, background, ideology, topics,
sensitive_topics, behavior_tendency{post_rate, reply_rate, repost_rate,
controversy_affinity}``. ``Agent.persona_traits`` is still a loose
``Mapping[str, Any]`` (Phase 1 hands these in via ``OntologyLoader``),
so the prompt layer does the work of hoisting the well-known keys to
predictable positions and giving the LLM explicit hints for the
behavior weights and the sensitive-topic avoidance list.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any

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
    "\n"
    "Choose ONE action. The action types are NOT interchangeable — pick the "
    "one that fits your intent. A healthy social feed has all of them, not "
    "just quote-replies. Field rules and selection guidance:\n"
    "  - CREATE_POST  → non-empty content; no targets. Use when you want to "
    "introduce a NEW topic, observation, or claim that is NOT already a "
    "thread in your feed — agenda-setting, not reaction.\n"
    "  - LIKE_POST    → target_post_id from your feed. Use when you agree "
    "with a post but have NOTHING substantive to add. A fast, lightweight "
    "signal — most agreement should be a like, not a quote.\n"
    "  - REPOST       → target_post_id from your feed. Use when a post "
    "deserves to spread beyond its current audience. Amplification without "
    "adding your own words — your followers will see it.\n"
    "  - QUOTE_POST   → target_post_id from your feed AND non-empty content. "
    "Use ONLY when you have a specific NEW angle, evidence, counterpoint, "
    "or qualification to add on top of an existing post. If your reply "
    "would just rephrase agreement, use LIKE_POST or REPOST instead. "
    "Quote-spam (everyone quoting every post) makes a feed feel artificial.\n"
    "  - FOLLOW       → target_agent_id of an author visible in your feed. "
    "Use when an author's stance or topics consistently align with yours "
    "and you want their future posts to keep surfacing in your feed. "
    "Following is how you actively shape your own information environment.\n"
    "  - DO_NOTHING   → omit all targets and content. Use only when nothing "
    "in your feed warrants any response."
)


# Persona-card layout, in render order. ``agent_id`` and ``topics`` are
# always emitted (they come from the ``Agent`` fields, not the trait
# bag); the rest are hoisted from ``persona_traits`` when present.
_PHASE1_PERSONA_KEYS: tuple[str, ...] = (
    "name",
    "entity_type",
    "personality",
    "speech_style",
    "background",
    "ideology",
    "sensitive_topics",
)

_BEHAVIOR_TENDENCY_LABELS: tuple[tuple[str, str], ...] = (
    ("post_rate", "originate posts"),
    ("reply_rate", "reply or quote"),
    ("repost_rate", "repost"),
    ("follow_rate", "follow others whose stance you find compelling"),
    ("controversy_affinity", "engage with controversy"),
)

# Phase 1 ontology 가 follow_rate 키를 빠뜨려도 LLM 이 가중치 신호를 받게
# 하는 안전망 (구버전 ontology 호환). Phase 1 의 BehaviorTendency.follow_rate
# 디폴트와 같은 값으로 맞춘다.
_FOLLOW_RATE_FALLBACK = 0.2


def compose_system(agent_id: str, context: ActionContext) -> str:
    """Build the system prompt — persona card + behavior hints + schema."""
    sections: list[str] = [
        _SYSTEM_HEADER.format(agent_id=agent_id),
        "Persona card:\n" + _persona_card(context),
    ]
    behavior = _behavior_hint(context)
    if behavior:
        sections.append(behavior)
    avoidance = _avoidance_hint(context)
    if avoidance:
        sections.append(avoidance)
    action_types = ", ".join(at.value for at in ActionType)
    sections.append(_SYSTEM_SCHEMA.format(action_types=action_types))
    sections.append("Respond with the JSON object only.")
    return "\n\n".join(sections)


def _persona_card(context: ActionContext) -> str:
    """Phase 1 keys hoisted to the top; unknown traits sink to ``extra_traits``."""
    agent = context.agent
    traits = dict(agent.persona_traits)
    card: dict[str, Any] = {
        "agent_id": agent.agent_id,
        "topics": list(agent.interests),
    }
    for key in _PHASE1_PERSONA_KEYS:
        if key in traits:
            card[key] = traits.pop(key)
    if "behavior_tendency" in traits:
        card["behavior_tendency"] = traits.pop("behavior_tendency")
    if traits:
        card["extra_traits"] = traits
    if agent.memory_summary:
        card["memory_summary"] = agent.memory_summary
    return json.dumps(card, ensure_ascii=False, indent=2)


def _behavior_hint(context: ActionContext) -> str:
    """Restate ``behavior_tendency`` weights in prose for a clearer LLM cue.

    Missing keys collapse to skip — *except* ``follow_rate``: if the
    ontology has a ``behavior_tendency`` block but omits ``follow_rate``
    (구버전 Phase 1 출력), inject the Phase 1 default so the LLM still
    sees a follow weight. Without this, the FOLLOW label is silently
    dropped and the model never picks FOLLOW.
    """
    bt = context.agent.persona_traits.get("behavior_tendency")
    if not isinstance(bt, Mapping):
        return ""
    bits: list[str] = []
    for key, label in _BEHAVIOR_TENDENCY_LABELS:
        if key in bt:
            bits.append(f"{label}: {bt[key]}")
        elif key == "follow_rate":
            bits.append(f"{label}: {_FOLLOW_RATE_FALLBACK}")
    if not bits:
        return ""
    return "Behavior tendencies (0..1, higher = more likely): " + "; ".join(bits) + "."


def _avoidance_hint(context: ActionContext) -> str:
    sensitive = context.agent.persona_traits.get("sensitive_topics")
    if not sensitive:
        return ""
    if isinstance(sensitive, str):
        listed = sensitive
    elif isinstance(sensitive, Mapping):
        return ""
    else:
        try:
            listed = ", ".join(str(t) for t in sensitive)
        except TypeError:
            return ""
    if not listed:
        return ""
    return f"Avoid initiating posts on these sensitive topics: {listed}."


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
