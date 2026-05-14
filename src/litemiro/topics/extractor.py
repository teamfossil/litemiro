"""``TopicExtractor`` — owned by **B**.

Maps the LLM-authored ``CREATE_POST`` content to the
``tuple[str, ...]`` topic field on ``Post``. The contract pinned by the
unit suite:

* The vocabulary is supplied at construction (typically Phase 1's
  topic taxonomy) and **pre-embedded once** so each ``extract`` call
  costs at most one ``EmbedderLike.embed`` for the content itself.
* Empty / whitespace-only content returns an empty tuple — the
  caller (round runner) then writes a ``Post`` with no topics rather
  than fabricating noise.
* Selection is the top-K vocabulary words whose cosine similarity
  with the content is at or above ``threshold``, ordered by score
  descending and word ascending for deterministic tie-breaks.
* Construction rejects ``top_k < 1``, threshold outside ``[0.0, 1.0]``,
  and empty vocabularies — these would all silently produce useless
  output otherwise.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from litemiro._vector import cosine

if TYPE_CHECKING:
    from collections.abc import Iterable

    from litemiro.interfaces import EmbedderLike


class TopicExtractor:
    def __init__(
        self,
        *,
        embedder: EmbedderLike,
        vocabulary: Iterable[str],
        top_k: int = 3,
        threshold: float = 0.3,
    ) -> None:
        if top_k < 1:
            raise ValueError(f"top_k must be >= 1, got {top_k}")
        if not 0.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in [0.0, 1.0], got {threshold}")
        cache: dict[str, tuple[float, ...]] = {}
        for word in vocabulary:
            if word and word not in cache:
                cache[word] = embedder.embed(word)
        if not cache:
            raise ValueError("vocabulary must contain at least one non-empty word")
        self._embedder = embedder
        self._top_k = top_k
        self._threshold = threshold
        self._vocab_embeddings = cache

    def extract(self, content: str) -> tuple[str, ...]:
        if not content.strip():
            return ()
        content_vec = self._embedder.embed(content)
        scored: list[tuple[float, str]] = []
        for word, vec in self._vocab_embeddings.items():
            score = cosine(content_vec, vec)
            if score >= self._threshold:
                scored.append((score, word))
        scored.sort(key=lambda item: (-item[0], item[1]))
        return tuple(word for _, word in scored[: self._top_k])


__all__ = ["TopicExtractor"]
