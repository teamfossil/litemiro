"""Phase 1 — OntologyA / OntologyB consistency validator."""

from __future__ import annotations

import statistics
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

            persona_topics = {topic for topic in profile.topics if topic}
            memory_topics = {topic for memory in store.semantic for topic in memory.topics if topic}
            if persona_topics & memory_topics:
                continue

            warnings.append(
                f"agent '{agent_id}' persona topics do not overlap semantic memory topics"
            )
        return warnings


__all__ = ["OntologyValidator", "ValidationResult"]
