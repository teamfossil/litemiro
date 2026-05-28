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
like_rate, follow_rate, controversy_affinity}``. ``Agent.persona_traits`` is still a loose
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
    "Choose ONE action. A realistic feed shows a mix of all action types — "
    "no single action should dominate. Match your choice to your actual "
    "intent this round, not to a generic 'reply to everything' habit. "
    "Concretely: if two or more of your recent actions were QUOTE_POST, "
    "default to LIKE_POST or REPOST this round unless you have genuinely "
    "new information to add — sustained QUOTE streaks are the single most "
    "common failure mode in this simulation.\n"
    "\n"
    "Two families:\n"
    "  • POST-REACTIONS (LIKE_POST, REPOST, QUOTE_POST) react to ONE post "
    "in your feed. Among these, LIKE is the lightest, REPOST amplifies, "
    "QUOTE adds your own substantive text. Most agreement should be a "
    "LIKE; reserve QUOTE for replies that genuinely add new information.\n"
    "  • AUTHORING & NETWORK (CREATE_POST, FOLLOW, DO_NOTHING) are not "
    "tied to a single feed post. Consider FOLLOW whenever you see an "
    "author whose stance keeps aligning with yours — it is a separate "
    "decision from reacting to a post, and your follow_rate should "
    "translate into actual FOLLOW actions over time, not zero.\n"
    "\n"
    "Field rules and selection guidance:\n"
    "  - LIKE_POST    → target_post_id from your feed. Use when you agree "
    "with a post or find it interesting and do NOT have a specific new "
    "angle worth a written reply. Liking is the normal, expected response "
    "for routine agreement — it is not a 'nothing-to-say' fallback.\n"
    "  - REPOST       → target_post_id from your feed. Use when a post "
    "deserves to spread beyond its current audience. Amplification without "
    "adding your own words — your followers will see it.\n"
    "  - QUOTE_POST   → target_post_id from your feed AND non-empty content. "
    "Use ONLY when your added text contributes specific NEW information: a "
    "counterargument, concrete evidence, a personal experience, or a "
    "meaningful qualification. Before picking this, ask: 'would a stranger "
    "reading my added text learn something they could not infer from the "
    "original post?' If no, use LIKE_POST instead. Rephrased agreement or "
    "restated points are likes, not quotes.\n"
    "  - CREATE_POST  → non-empty content; no targets. Use when you want to "
    "introduce a NEW topic, observation, or claim that is NOT already a "
    "thread in your feed — agenda-setting, not reaction. Originating new "
    "content is a separate axis from reacting to the feed: even when your "
    "feed is full of relevant posts, your post_rate translates into "
    "CREATE_POST over time. Once cold-start (the first empty-feed round) "
    "ends, never selecting CREATE_POST contradicts the weight.\n"
    "  - FOLLOW       → target_agent_id of an author visible in your feed. "
    "Use when an author's stance or topics consistently align with yours "
    "and you want their future posts to keep surfacing in your feed. This "
    "is a separate decision from reacting to a specific post — pick FOLLOW "
    "when shaping your network matters more this round than reacting to "
    "any one post. Skipping FOLLOW entirely contradicts your follow_rate.\n"
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
    # ``reply_rate`` 는 ActionType 에 REPLY 가 없어서 옛 라벨 "reply or quote"
    # 가 QUOTE 한 곳으로만 신호를 몰아 debug3 에서 QUOTE 57% 쏠림을 만들었다.
    # 본 모델의 reaction 은 LIKE / REPOST / QUOTE 셋이므로 라벨도 셋을 모두
    # 가리키게 풀어 신호를 분산.
    ("reply_rate", "react to others' posts overall (LIKE / REPOST / QUOTE)"),
    ("repost_rate", "repost"),
    ("like_rate", "press LIKE on aligned posts"),
    ("follow_rate", "follow others whose stance you find compelling"),
    ("controversy_affinity", "engage with controversy"),
)

# Phase 1 ontology 가 follow_rate / like_rate 키를 빠뜨려도 LLM 이 가중치
# 신호를 받게 하는 안전망 — Phase 1 신버전은 항상 키를 채우지만 외부에서
# 직접 박은 ontology JSON 이나 구버전 산출물 (follow_rate=#106 이전,
# like_rate=#10 이전) 호환을 위해 살려둔다. 값은
# ``phase1.models.BehaviorTendency`` 디폴트와 동기 유지 — Phase 1 디폴트를
# 바꿀 때 같이 갱신해야 silent drift 가 없다.
_FOLLOW_RATE_FALLBACK = 0.2
_LIKE_RATE_FALLBACK = 0.4


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

    Missing keys collapse to skip — *except* ``follow_rate`` / ``like_rate``:
    if the ontology has a ``behavior_tendency`` block but omits either key
    (구버전 Phase 1 출력), inject the Phase 1 default so the LLM still sees
    a weight. Without this, the matching action label is silently dropped
    and the model never picks that action.
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
        elif key == "like_rate":
            bits.append(f"{label}: {_LIKE_RATE_FALLBACK}")
    if not bits:
        return ""
    line = "Behavior tendencies (0..1, higher = more likely): " + "; ".join(bits) + "."
    # reply_rate 와 like_rate / repost_rate 가 동시에 등장하면 LLM 이 둘을 곱해
    # 야 할지 (중복 가중) umbrella+subtype 으로 봐야 할지 모호 — #120 리뷰. 첫
    # 보강 (umbrella/tilt 표현, #120) 도 "tilt within that umbrella" 가 ratio /
    # 곱 / absolute 어느 셋인지 갈라져 LLM 별 분포가 흔들렸다 (#122). 의도된 산수
    # 를 직접 박는다: reply_rate 가 총량, like_rate 와 repost_rate 가 그 안의
    # absolute share, 나머지 = QUOTE. debug4 의 LIKE 41% / QUOTE 29% 도 이
    # 해석에 정합 (Phase 1 default 0.668/0.4/0.2 → LIKE 60%, REPOST 30%, QUOTE
    # 10% 의 noisy 근사).
    if "reply_rate" in bt:
        line += (
            " Note: reply_rate is the total reaction probability split across "
            "LIKE / REPOST / QUOTE. like_rate and repost_rate are absolute weights "
            "within that total — the remainder (reply_rate - like_rate - repost_rate) "
            "goes to QUOTE."
        )
    # post_rate 는 reply_rate 와 직교 — CREATE_POST 의 절대 비율. debug4 측정에서
    # post_rate default 0.5 인데도 r1 이후 CREATE_POST ≈ 0 으로 떨어지는 cold-
    # start 후 망각 패턴이 보였다. umbrella 산수가 reaction 셋의 분배만 명시하고
    # post_rate 가 별도 축이라는 점이 명시 안 돼 LLM 이 feed 가 차면 reaction 만
    # 골라버린다 — 두 축이 직교라는 cue 를 한 줄 더 박는다.
    if "post_rate" in bt:
        line += (
            " post_rate is a separate axis from reply_rate — it controls how often "
            "you ORIGINATE a new post, independent of feed contents. A non-trivial "
            "post_rate (>0.2) that yields zero CREATE_POST after cold-start signals "
            "the weight is being ignored."
        )
    return line


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
