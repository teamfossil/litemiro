"""Wire-format JSON Schemas — packaged with the wheel."""

from __future__ import annotations

import json
from importlib import resources
from typing import Any

ROUND_EVENT_RESOURCE = "round_event.schema.json"


def round_event_schema() -> dict[str, Any]:
    """Load the bundled RoundEvent JSON Schema."""
    text = resources.files(__package__).joinpath(ROUND_EVENT_RESOURCE).read_text(encoding="utf-8")
    schema: dict[str, Any] = json.loads(text)
    return schema


__all__ = ["ROUND_EVENT_RESOURCE", "round_event_schema"]
