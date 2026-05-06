"""Prompt templates owned by **B**.

Kept as plain Python so they round-trip through type checking and
unit-testable composition without needing a templating engine.
"""

from __future__ import annotations

from litemiro.prompts.action_selector import compose_system, compose_user

__all__ = ["compose_system", "compose_user"]
