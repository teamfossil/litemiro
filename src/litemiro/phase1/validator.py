"""Phase 1 — OntologyA / OntologyB consistency validator."""

from __future__ import annotations

import re
import statistics
from collections.abc import Iterable
from dataclasses import dataclass, field

import structlog

from litemiro.phase1.models import OntologyA, OntologyB

log = structlog.get_logger(__name__)


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class OntologyValidator:
    def validate(self, a: OntologyA, b: OntologyB) -> ValidationResult:
        errors: list[str] = []
        warnings: list[str] = []

        errors.extend(self._check_agent_id_match(a, b))
        errors.extend(self._check_required_fields(a))
        errors.extend(self._check_value_ranges(a))
        errors.extend(self._check_referential_integrity(a))
        errors.extend(self._check_memory_references(a, b))
        warnings.extend(self._check_ideology_distribution(a))
        warnings.extend(self._check_persona_memory_topic_overlap(a, b))

        result = ValidationResult(valid=len(errors) == 0, errors=errors, warnings=warnings)
        log.info(
            "validation_complete",
            valid=result.valid,
            error_count=len(errors),
            warning_count=len(warnings),
        )
        return result

    def _check_agent_id_match(self, a: OntologyA, b: OntologyB) -> list[str]:
        a_ids = set(a.agents)
        b_ids = set(b.stores)
        errors: list[str] = []
        only_in_a = a_ids - b_ids
        only_in_b = b_ids - a_ids
        if only_in_a:
            errors.append(f"agent_ids in OntologyA but not in OntologyB: {sorted(only_in_a)}")
        if only_in_b:
            errors.append(f"agent_ids in OntologyB but not in OntologyA: {sorted(only_in_b)}")
        return errors

    def _check_required_fields(self, a: OntologyA) -> list[str]:
        errors: list[str] = []
        for agent_id, profile in a.agents.items():
            missing: list[str] = []
            if not profile.skeleton:
                missing.append("skeleton")
            if profile.ideology is None:
                missing.append("ideology")
            if not profile.topics:
                missing.append("topics")
            if profile.behavior_tendency is None:
                missing.append("behavior_tendency")
            if missing:
                errors.append(
                    f"agent '{agent_id}' missing required fields (defaults applied): {missing}"
                )
        return errors

    def _check_value_ranges(self, a: OntologyA) -> list[str]:
        errors: list[str] = []
        for agent_id, profile in a.agents.items():
            ideology = profile.ideology
            if not (0.0 <= ideology <= 1.0):
                errors.append(
                    f"agent '{agent_id}' ideology={ideology} out of [0,1] (will be clamped)"
                )
            bt = profile.behavior_tendency
            for rate_name, rate_val in (
                ("post_rate", bt.post_rate),
                ("reply_rate", bt.reply_rate),
                ("repost_rate", bt.repost_rate),
                ("like_rate", bt.like_rate),
                ("follow_rate", bt.follow_rate),
                ("controversy_affinity", bt.controversy_affinity),
            ):
                if not (0.0 <= rate_val <= 1.0):
                    errors.append(
                        f"agent '{agent_id}' {rate_name}={rate_val} out of [0,1] (will be clamped)"
                    )
        return errors

    def _check_referential_integrity(self, a: OntologyA) -> list[str]:
        errors: list[str] = []
        valid_ids = set(a.agents)
        for agent_id, profile in a.agents.items():
            invalid = [fid for fid in profile.initial_following if fid not in valid_ids]
            if invalid:
                errors.append(
                    f"agent '{agent_id}' initial_following references unknown agent_ids"
                    f" (will be removed): {invalid}"
                )
        return errors

    def _check_memory_references(self, a: OntologyA, b: OntologyB) -> list[str]:
        errors: list[str] = []
        valid_ids = set(a.agents)
        for store_id, store in b.stores.items():
            for memory in store.semantic:
                invalid = [
                    rel.agent_id
                    for rel in memory.key_relationships
                    if rel.agent_id not in valid_ids
                ]
                if invalid:
                    errors.append(
                        f"memory '{memory.id}' in store '{store_id}' key_relationships "
                        f"reference unknown agent_ids: {invalid}"
                    )
        return errors

    def _check_ideology_distribution(self, a: OntologyA) -> list[str]:
        if not a.agents:
            return []
        ideologies = [p.ideology for p in a.agents.values()]
        mean = statistics.mean(ideologies)
        warnings: list[str] = []
        if mean < 0.3:
            warnings.append(
                f"ideology distribution skewed left (mean={mean:.3f} < 0.3); "
                "simulation may lack ideological diversity"
            )
        elif mean > 0.7:
            warnings.append(
                f"ideology distribution skewed right (mean={mean:.3f} > 0.7); "
                "simulation may lack ideological diversity"
            )
        return warnings

    def _check_persona_memory_topic_overlap(self, a: OntologyA, b: OntologyB) -> list[str]:
        warnings: list[str] = []
        for agent_id, profile in a.agents.items():
            store = b.stores.get(agent_id)
            if store is None or not store.semantic:
                continue

            persona_topics = _normalized_topic_set(profile.topics)
            memory_topics = _normalized_topic_set(
                topic for memory in store.semantic for topic in memory.topics
            )
            if _has_topic_overlap(persona_topics, memory_topics):
                continue

            warnings.append(
                f"agent '{agent_id}' persona topics do not overlap semantic memory topics"
            )
        return warnings


# Persona 토픽은 LLM 이 생성한 다어절 명사구 ("AI 윤리 가이드라인") 인 반면
# memory 토픽은 `_KEYWORD_RE` 로 추출된 단일 명사 또는 CamelCase entity type
# ("AI", "AIProduct") 이라 whole-string casefold set 비교로는 사실상 절대
# 매칭이 안 된다 (run 한 번에 100 중 19 falsy 경고). 두 묶음을 단어 단위
# 토큰으로 정규화한 뒤 set 교집합으로 비교한다:
#   1. `\w+` 로 공백·구두점·슬래시·하이픈 단위로 분리
#   2. 한글 조사/어미 strip — 메모리 측은 이미 하지만 페르소나 측도 보호
#   3. ASCII 토큰은 CamelCase 분해 ("AIProduct" → "AI", "Product")
# 길이 1 토큰은 의미 노이즈가 커 제외.
_TOKEN_RE = re.compile(r"\w+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z][a-z]+|[A-Z]+|[a-z]+|\d+")
_KOREAN_SUFFIXES: tuple[str, ...] = tuple(
    sorted(
        (
            "에서는",
            "에서도",
            "한테는",
            "한테도",
            "에게는",
            "에게도",
            "에서",
            "에게",
            "한테",
            "부터",
            "까지",
            "보다",
            "마저",
            "처럼",
            "으로",
            "라도",
            "들이",
            "들은",
            "들을",
            "들의",
            "들도",
            "들과",
            "하는",
            "되는",
            "이며",
            "이고",
            "는",
            "은",
            "이",
            "가",
            "을",
            "를",
            "의",
            "에",
            "도",
            "만",
            "와",
            "과",
            "로",
            "야",
        ),
        key=len,
        reverse=True,
    )
)


def _strip_korean_suffix(token: str) -> str:
    if token.isascii():
        return token
    for suffix in _KOREAN_SUFFIXES:
        if token.endswith(suffix):
            stem = token[: -len(suffix)]
            if len(stem) >= 2:
                return stem
    return token


def _normalized_topic_set(topics: Iterable[str]) -> set[str]:
    tokens: set[str] = set()
    for topic in topics:
        cleaned = topic.strip()
        if not cleaned:
            continue
        for raw in _TOKEN_RE.findall(cleaned):
            stem = _strip_korean_suffix(raw)
            parts = _CAMEL_RE.findall(stem) if stem.isascii() else [stem]
            for part in parts:
                if len(part) >= 2:
                    tokens.add(part.casefold())
    return tokens


def _has_topic_overlap(persona: set[str], memory: set[str]) -> bool:
    """Exact 토큰 교집합 우선, 없으면 한국어 합성어용 substring fallback.

    한국어는 ' 개인정보 ' 와 ' 개인정보보호위원회 ' 처럼 단어 경계 없이
    의미 단위가 합쳐지는 합성어가 흔해 단순 토큰 분리로는 잡히지 않는다
    (run debug3 의 gdpr_guideline 케이스). 토큰 길이 3 이상에서만 substring
    을 인정해 두 글자 ASCII 약어 ('ai', 'eu') 의 거짓 양성을 막는다 — 그
    범주는 이미 CamelCase 분해로 충분히 매칭된다.
    """
    if persona & memory:
        return True
    for p in persona:
        if len(p) < 3:
            continue
        for m in memory:
            if len(m) < 3:
                continue
            if p in m or m in p:
                return True
    return False


__all__ = ["OntologyValidator", "ValidationResult"]
