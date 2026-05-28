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

# originate 축 (CREATE_POST / FOLLOW) 은 ``ActionSelector`` 의 확률 게이트가
# post_rate / follow_rate 로 직접 강제하므로 prompt 에서 prose cue 로 설득하지
# 않는다 (#120~#150 의 prose-only 시도가 FOLLOW 1.8% / r1+ CREATE_POST 0 으로
# 실패). 여기 남는 건 reaction 축 (LIKE / REPOST / QUOTE) 의 상대 가중 — feed
# 를 보고 고르는 결정이라 LLM 자율로 두되 비율 신호만 정확히 준다.
#
# 옛 산수 (``QUOTE = reply_rate - like_rate - repost_rate``) 는 폐기. Phase 1
# 페르소나의 97% 에서 like_rate + repost_rate 가 reply_rate 를 넘어 QUOTE 에
# 음수 확률을 배정하는 불가능한 지시였다. 대신 like_rate : repost_rate :
# controversy_affinity 를 정규화한 share 를 준다 — 셋 다 [0,1] 양수라 음수 불가.
#
# Fallback 은 ``phase1.models.BehaviorTendency`` 디폴트와 동기 유지 (구버전
# ontology / 외부 주입 JSON 의 키 누락 대비). Phase 1 디폴트를 바꿀 때 같이
# 갱신해야 silent drift 가 없다.
_LIKE_RATE_FALLBACK = 0.4
_REPOST_RATE_FALLBACK = 0.2
_CONTROVERSY_FALLBACK = 0.5

# _authors_block 의 author 별 sample post snippet 길이. feed_block 의 120 보다
# 짧게 — author 섹션은 여러 author 가 나란히 있어 줄당 길이 압축이 필요하고,
# stance 추론에는 첫 문장 정도면 충분. #142 hub pattern 완화용 stance hint.
_SAMPLE_LEN = 80


def compose_system(
    agent_id: str,
    context: ActionContext,
    *,
    forced_family: ActionType | None = None,
    react_only: bool = False,
) -> str:
    """Build the system prompt — persona card + behavior hints + schema.

    ``forced_family`` / ``react_only`` are set by ``ActionSelector`` 's
    behavior gate. With both at their defaults (no gate — e.g. unit tests
    that omit ``global_seed``) the schema section is byte-identical to the
    pre-gate prompt, so the composition contract is preserved.

    * ``forced_family`` (CREATE_POST or FOLLOW) → the gate has already drawn
      this action from post_rate / follow_rate; the schema collapses to a
      single mandated type.
    * ``react_only`` → the gate landed on the reaction branch; CREATE_POST
      and FOLLOW are dropped from the allowed set so they only ever surface
      through their probability gates, not opportunistically.
    """
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
    sections.append(_schema_section(forced_family=forced_family, react_only=react_only))
    sections.append("Respond with the JSON object only.")
    return "\n\n".join(sections)


# reaction 분기에서 originate 두 종을 뺀 허용 집합. 게이트가 CREATE_POST /
# FOLLOW 를 확률로 전담하므로, LLM 이 feed 가 차 있다고 즉흥적으로 글을 쓰거나
# 팔로우하지 않게 vocabulary 자체에서 제외한다.
_REACTION_TYPES: tuple[ActionType, ...] = (
    ActionType.LIKE_POST,
    ActionType.REPOST,
    ActionType.QUOTE_POST,
    ActionType.DO_NOTHING,
)

_REACT_ONLY_PREFIX = (
    "This round, originating a new post and following an author are decided "
    "separately and are NOT options now — choose only how you react to a post "
    "already in your feed (or DO_NOTHING if nothing warrants a reaction).\n\n"
)

_FORCE_CREATE_POST = (
    "This round you are ORIGINATING new content — that decision is already made "
    "for you. Output EXACTLY one JSON object:\n"
    '  {"type": "CREATE_POST", "target_post_id": null, "target_agent_id": null, '
    '"content": <str>}\n'
    "content must be non-empty: introduce a NEW topic, observation, or claim in "
    "your own voice, consistent with your persona and topics — agenda-setting, "
    "not a reaction to any feed post. Do NOT output any other action type."
)

_FORCE_FOLLOW = (
    "This round you are FOLLOWING an author — that decision is already made for "
    "you. Output EXACTLY one JSON object:\n"
    '  {"type": "FOLLOW", "target_post_id": null, "target_agent_id": <author_id>, '
    '"content": null}\n'
    "Pick ONE author visible in your feed whose stance or topics align with "
    "yours. target_agent_id must be a feed author and must not be yourself. "
    "Do NOT output any other action type."
)


def _schema_section(*, forced_family: ActionType | None, react_only: bool) -> str:
    """Render the output-schema block, narrowed by the gate decision."""
    if forced_family is ActionType.CREATE_POST:
        return _FORCE_CREATE_POST
    if forced_family is ActionType.FOLLOW:
        return _FORCE_FOLLOW
    types = _REACTION_TYPES if react_only else tuple(ActionType)
    body = _SYSTEM_SCHEMA.format(action_types=", ".join(at.value for at in types))
    return (_REACT_ONLY_PREFIX + body) if react_only else body


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


def _as_rate(value: object, fallback: float) -> float:
    """Coerce a behavior-tendency value to a [0,1] float, else the fallback.

    External / hand-written ontology JSON can carry non-numeric or
    out-of-range weights; the normalization must not raise on them.
    """
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        v = float(value)
        return v if 0.0 <= v <= 1.0 else fallback
    return fallback


def _behavior_hint(context: ActionContext) -> str:
    """Reaction-mix cue — LIKE / REPOST / QUOTE share for the reaction branch.

    Normalized from ``like_rate : repost_rate : controversy_affinity`` (all
    [0,1], so the share is always well-defined — no negative QUOTE remainder).
    The originate axis (CREATE_POST / FOLLOW) is handled by ``ActionSelector`` 's
    probability gate, not here. When the persona carries no ``behavior_tendency``
    block the cue is skipped — the persona card already embeds the raw JSON and
    the gate falls back on its own defaults.
    """
    bt = context.agent.persona_traits.get("behavior_tendency")
    if not isinstance(bt, Mapping):
        return ""
    like = _as_rate(bt.get("like_rate"), _LIKE_RATE_FALLBACK)
    repost = _as_rate(bt.get("repost_rate"), _REPOST_RATE_FALLBACK)
    contro = _as_rate(bt.get("controversy_affinity"), _CONTROVERSY_FALLBACK)
    total = like + repost + contro
    if total <= 0:
        return ""
    pct = [round(100 * w / total) for w in (like, repost, contro)]
    return (
        "When you react to a feed post, your persona leans roughly "
        f"LIKE {pct[0]}% / REPOST {pct[1]}% / QUOTE {pct[2]}% "
        "(normalized from like_rate / repost_rate / controversy_affinity). "
        "LIKE is the default for routine agreement; REPOST amplifies a post "
        "without adding words; reserve QUOTE for added text that contributes "
        "genuinely new information."
    )


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


def _authors_block(context: ActionContext) -> str:
    """Feed 의 author 들을 별도로 묶어 노출. 각 author 가 몇 개 post 로
    등장했는지 + 이미 follow 중인지 + 그 author 의 대표 post snippet 을
    표시 — LLM 이 새 FOLLOW 후보 (아직 follow 안 했고 stance 정합인
    author) 를 식별할 수 있게 한다. snippet 은 feed 의 hot order 에서
    그 author 가 처음 등장한 post (= 그 author 의 가장 hot 한 post) 의
    첫 ``_SAMPLE_LEN`` 글자. agent 의 ideology 는 사적 정보라 표시 불가
    — 대신 post 본문으로 stance 추론. #142 의 hub pattern (stance
    mismatch FOLLOW 22%) 완화. 강제가 아니라 정보 제공 — FOLLOW 결정은
    여전히 모델의 판단에 맡긴다.
    """
    if not context.feed:
        return ""
    self_id = context.agent.agent_id
    counts: dict[str, int] = {}
    order: list[str] = []
    sample: dict[str, str] = {}
    for post in context.feed:
        author = post.author_id
        if author == self_id:
            continue
        if author not in counts:
            order.append(author)
            sample[author] = post.content[:_SAMPLE_LEN]
        counts[author] = counts.get(author, 0) + 1
    if not counts:
        return ""
    following = context.following_ids
    lines = ["Authors in your feed (FOLLOW candidates are those not yet followed):"]
    for author in sorted(order, key=lambda a: (-counts[a], a)):
        tag = "already following" if author in following else "not yet followed"
        lines.append(f"  - @{author} ({counts[author]} posts) — {tag}")
        lines.append(f"      sample: {sample[author]}")
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
    sections = [
        f"Round: {context.round_num}",
        f"Followers: {context.follower_count}, Following: {context.following_count}",
        _feed_block(context),
    ]
    authors = _authors_block(context)
    if authors:
        sections.append(authors)
    sections.extend([_recent_block(context), "Choose your next action."])
    return "\n\n".join(sections)


__all__ = ["compose_system", "compose_user"]
