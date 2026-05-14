"""Internal numeric helpers shared across B's modules.

Kept private (leading underscore) — the public surface is the
``EmbedderLike`` protocol; this module just centralizes the cosine math
so ``feed.engine`` and ``topics.extractor`` don't drift.
"""

from __future__ import annotations

import math


def cosine(a: tuple[float, ...], b: tuple[float, ...]) -> float:
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(y * y for y in b))
    if norm_a == 0.0 or norm_b == 0.0:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    return dot / (norm_a * norm_b)


__all__ = ["cosine"]
