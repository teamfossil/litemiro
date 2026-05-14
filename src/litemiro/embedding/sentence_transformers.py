"""``STEmbedder`` — production ``EmbedderLike`` adapter (W3).

Wraps ``sentence-transformers`` so ``FeedEngine`` and
``TopicExtractor`` see vectors as ``tuple[float, ...]``. The model is
loaded **lazily** on the first ``embed`` call, not at construction —
importing this module never reaches into ``sentence_transformers``,
keeping startup cheap and letting unit tests patch ``_load_model``
without paying for real weights.

The default ``all-MiniLM-L6-v2`` is the same 384-dim model the Phase
2 design doc names; vectors are L2-normalised so cosine similarity
collapses to a dot product downstream.
"""

from __future__ import annotations

import threading
from typing import Any


class STEmbedder:
    def __init__(self, *, model_name: str = "all-MiniLM-L6-v2") -> None:
        self._model_name = model_name
        self._model: Any | None = None
        self._lock = threading.Lock()

    @property
    def model_name(self) -> str:
        return self._model_name

    def embed(self, text: str) -> tuple[float, ...]:
        model = self._ensure_model()
        vector = model.encode(text, normalize_embeddings=True)
        return tuple(float(x) for x in vector.tolist())

    def _ensure_model(self) -> Any:
        if self._model is None:
            with self._lock:
                if self._model is None:
                    self._model = self._load_model()
        return self._model

    def _load_model(self) -> Any:
        # Imported lazily so the optional `embedding` extras only need
        # to be installed when the adapter actually runs.
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        except ImportError as exc:
            raise ImportError(
                "STEmbedder requires the 'sentence-transformers' package. "
                "Install it with: pip install 'litemiro[embedding]'"
            ) from exc
        return SentenceTransformer(self._model_name)


__all__ = ["STEmbedder"]
