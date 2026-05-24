"""Wire-format JSON Schemas — packaged with the wheel."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

ROUND_EVENT_RESOURCE = "round_event.schema.json"
ONTOLOGY_A_RESOURCE = "ontology_a.schema.json"
ONTOLOGY_B_RESOURCE = "ontology_b.schema.json"


def _load(name: str) -> dict[str, Any]:
    text = resources.files(__package__).joinpath(name).read_text(encoding="utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


def round_event_schema() -> dict[str, Any]:
    """Load the bundled RoundEvent JSON Schema."""
    return _load(ROUND_EVENT_RESOURCE)


def ontology_a_schema() -> dict[str, Any]:
    """Load the bundled OntologyA JSON Schema."""
    return _load(ONTOLOGY_A_RESOURCE)


def ontology_b_schema() -> dict[str, Any]:
    """Load the bundled OntologyB JSON Schema."""
    return _load(ONTOLOGY_B_RESOURCE)


__all__ = [
    "ONTOLOGY_A_RESOURCE",
    "ONTOLOGY_B_RESOURCE",
    "ROUND_EVENT_RESOURCE",
    "ontology_a_schema",
    "ontology_b_schema",
    "round_event_schema",
]
