from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from typing import Any

from litemiro.phase1.models import AgentProfile, Entity, Ontology, PersonaMode

_CAMEL_BOUNDARY_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")

_DIRECT_PERSON_TERMS = {
    "academic",
    "activist",
    "advocate",
    "analyst",
    "artist",
    "author",
    "ceo",
    "citizen",
    "consumer",
    "creator",
    "cto",
    "delegate",
    "developer",
    "doctor",
    "editor",
    "employee",
    "engineer",
    "expert",
    "executive",
    "founder",
    "governor",
    "human",
    "individual",
    "influencer",
    "journalist",
    "lawyer",
    "lawmaker",
    "mayor",
    "minister",
    "official",
    "person",
    "politician",
    "professor",
    "reporter",
    "representative",
    "researcher",
    "resident",
    "senator",
    "spokesperson",
    "student",
    "teacher",
    "user",
    "worker",
}

_REPRESENTATIVE_ENTITY_TERMS = {
    "agency",
    "alliance",
    "association",
    "broadcaster",
    "business",
    "center",
    "centre",
    "coalition",
    "commission",
    "committee",
    "community",
    "company",
    "corporation",
    "corp",
    "council",
    "department",
    "enterprise",
    "foundation",
    "government",
    "group",
    "industry",
    "institution",
    "institute",
    "laboratory",
    "lab",
    "media",
    "ministry",
    "newspaper",
    "ngo",
    "nonprofit",
    "office",
    "organization",
    "org",
    "party",
    "press",
    "publisher",
    "school",
    "society",
    "startup",
    "union",
    "university",
}

_CONTEXT_ONLY_TERMS = {
    "act",
    "agenda",
    "algorithm",
    "bill",
    "charter",
    "code",
    "concept",
    "data",
    "dataset",
    "document",
    "event",
    "framework",
    "guideline",
    "initiative",
    "issue",
    "law",
    "market",
    "model",
    "paper",
    "plan",
    "policy",
    "principle",
    "product",
    "program",
    "project",
    "proposal",
    "regulation",
    "regulatory",
    "report",
    "rule",
    "service",
    "standard",
    "strategy",
    "system",
    "technology",
    "topic",
    "treaty",
}

_HARD_CONTEXT_TERMS = {
    "act",
    "agenda",
    "bill",
    "charter",
    "code",
    "concept",
    "document",
    "framework",
    "guideline",
    "issue",
    "law",
    "policy",
    "principle",
    "regulation",
    "regulatory",
    "rule",
    "standard",
    "topic",
    "treaty",
}


class ActorClassifier:
    def __init__(self, ontology: Ontology) -> None:
        self._type_defs = {
            _normalize_type(type_def.name): type_def for type_def in ontology.entity_types
        }

    def persona_mode_for_entity(self, entity: Entity) -> PersonaMode:
        type_def = self._type_defs.get(_normalize_type(entity.type))
        if type_def and type_def.persona_mode is not None:
            return type_def.persona_mode

        return infer_persona_mode(
            type_name=entity.type,
            name=entity.name,
            description=type_def.description if type_def else "",
            attributes=entity.attributes,
            summary=entity.summary,
        )

    def persona_mode_for_profile(self, profile: AgentProfile) -> PersonaMode:
        type_def = self._type_defs.get(_normalize_type(profile.entity_type))
        if type_def and type_def.persona_mode is not None:
            return type_def.persona_mode

        attributes: Mapping[str, Any] = {}
        raw_attributes = profile.skeleton.get("attributes")
        if isinstance(raw_attributes, Mapping):
            attributes = raw_attributes

        return infer_persona_mode(
            type_name=profile.entity_type,
            name=profile.name,
            description=type_def.description if type_def else "",
            attributes=attributes,
            summary=profile.background,
        )


def infer_persona_mode(
    *,
    type_name: str,
    name: str = "",
    description: str = "",
    attributes: Mapping[str, Any] | None = None,
    summary: str = "",
) -> PersonaMode:
    type_mode = _persona_mode_from_type_tokens(_tokens(type_name))
    if type_mode is not None:
        return type_mode

    tokens = _tokens(
        type_name,
        name,
        description,
        summary,
        *(attributes or {}).keys(),
        *_flatten_attribute_values((attributes or {}).values()),
    )

    if tokens & _DIRECT_PERSON_TERMS:
        return PersonaMode.DIRECT_PERSON

    context_score = len(tokens & _CONTEXT_ONLY_TERMS)
    representative_score = len(tokens & _REPRESENTATIVE_ENTITY_TERMS)
    if context_score > 0 and context_score >= representative_score:
        return PersonaMode.CONTEXT_ONLY
    if representative_score > 0:
        return PersonaMode.REPRESENTATIVE
    return PersonaMode.CONTEXT_ONLY


def _persona_mode_from_type_tokens(tokens: set[str]) -> PersonaMode | None:
    if tokens & _DIRECT_PERSON_TERMS:
        return PersonaMode.DIRECT_PERSON
    if tokens & _HARD_CONTEXT_TERMS:
        return PersonaMode.CONTEXT_ONLY
    if tokens & _REPRESENTATIVE_ENTITY_TERMS:
        return PersonaMode.REPRESENTATIVE
    return None


def representative_role_for_entity(entity: Entity) -> str:
    tokens = _tokens(entity.type, entity.name)
    if tokens & {"media", "newspaper", "press", "publisher", "broadcaster"}:
        return "journalist"
    if tokens & {"agency", "government", "ministry", "department", "commission", "committee"}:
        return "public official"
    if tokens & {"company", "corporation", "corp", "business", "enterprise", "startup"}:
        return "company representative"
    if tokens & {"university", "school", "institution", "institute", "laboratory", "lab"}:
        return "institution representative"
    return "spokesperson"


def _tokens(*values: object) -> set[str]:
    result: set[str] = set()
    for value in values:
        text = str(value or "")
        if not text:
            continue
        text = _CAMEL_BOUNDARY_RE.sub(" ", text)
        result.update(token.casefold() for token in _TOKEN_RE.findall(text))
    return result


def _flatten_attribute_values(values: Iterable[Any]) -> list[str]:
    flattened: list[str] = []
    for value in values:
        if isinstance(value, str):
            flattened.append(value)
        elif isinstance(value, Mapping):
            flattened.extend(_flatten_attribute_values(value.values()))
        elif isinstance(value, Iterable) and not isinstance(value, bytes):
            flattened.extend(str(item) for item in value)
    return flattened


def _normalize_type(value: str) -> str:
    return "".join(sorted(_tokens(value)))


__all__ = [
    "ActorClassifier",
    "infer_persona_mode",
    "representative_role_for_entity",
]
