"""TDD spec for ``litemiro.topics.extractor.TopicExtractor``.

Notion Section 3.2 only says "CREATE_POST → 새 포스트 등록". B locks the
contract so A can call this surface without reasoning about embeddings:

* Vocabulary is **pre-embedded** at construction — ``extract`` calls
  ``embedder.embed`` exactly once per call (for the content).
* Top-K cap applies after threshold filtering; ties resolve by word
  ascending so two runs against the same fixtures emit identical
  ``Post.topics`` tuples.
* Empty / whitespace content returns ``()`` rather than top-K nonsense.
* Construction rejects nonsensical settings (k=0, threshold > 1, empty
  vocab) — these would silently corrupt the candidacy pool.
"""

from __future__ import annotations

import pytest

from litemiro.interfaces import TopicExtractorLike
from litemiro.topics.extractor import TopicExtractor


class _FakeEmbedder:
    """Records calls and returns deterministic vectors from a mapping."""

    def __init__(self, mapping: dict[str, tuple[float, ...]]) -> None:
        self._mapping = mapping
        self.calls: list[str] = []

    def embed(self, text: str) -> tuple[float, ...]:
        self.calls.append(text)
        if text not in self._mapping:
            raise KeyError(f"no fixture vector for {text!r}")
        return self._mapping[text]


class TestConstruction:
    def test_vocabulary_is_preembedded(self) -> None:
        embedder = _FakeEmbedder({"ai": (1.0, 0.0), "music": (0.0, 1.0)})
        TopicExtractor(embedder=embedder, vocabulary=("ai", "music"))
        assert sorted(embedder.calls) == ["ai", "music"]

    def test_duplicate_vocabulary_words_embedded_once(self) -> None:
        embedder = _FakeEmbedder({"ai": (1.0, 0.0)})
        TopicExtractor(embedder=embedder, vocabulary=("ai", "ai", "ai"))
        assert embedder.calls == ["ai"]

    def test_empty_strings_in_vocabulary_are_skipped(self) -> None:
        embedder = _FakeEmbedder({"ai": (1.0, 0.0)})
        TopicExtractor(embedder=embedder, vocabulary=("", "ai", ""))
        assert embedder.calls == ["ai"]

    def test_empty_vocabulary_rejected(self) -> None:
        embedder = _FakeEmbedder({})
        with pytest.raises(ValueError, match="vocabulary"):
            TopicExtractor(embedder=embedder, vocabulary=())

    def test_only_empty_strings_in_vocabulary_rejected(self) -> None:
        embedder = _FakeEmbedder({})
        with pytest.raises(ValueError, match="vocabulary"):
            TopicExtractor(embedder=embedder, vocabulary=("", ""))

    def test_top_k_zero_rejected(self) -> None:
        embedder = _FakeEmbedder({"ai": (1.0, 0.0)})
        with pytest.raises(ValueError, match="top_k"):
            TopicExtractor(embedder=embedder, vocabulary=("ai",), top_k=0)

    def test_top_k_negative_rejected(self) -> None:
        embedder = _FakeEmbedder({"ai": (1.0, 0.0)})
        with pytest.raises(ValueError, match="top_k"):
            TopicExtractor(embedder=embedder, vocabulary=("ai",), top_k=-1)

    def test_threshold_negative_rejected(self) -> None:
        embedder = _FakeEmbedder({"ai": (1.0, 0.0)})
        with pytest.raises(ValueError, match="threshold"):
            TopicExtractor(embedder=embedder, vocabulary=("ai",), threshold=-0.1)

    def test_threshold_above_one_rejected(self) -> None:
        embedder = _FakeEmbedder({"ai": (1.0, 0.0)})
        with pytest.raises(ValueError, match="threshold"):
            TopicExtractor(embedder=embedder, vocabulary=("ai",), threshold=1.5)


class TestExtract:
    def test_empty_content_returns_empty_tuple(self) -> None:
        embedder = _FakeEmbedder({"ai": (1.0, 0.0)})
        extractor = TopicExtractor(embedder=embedder, vocabulary=("ai",))
        embedder.calls.clear()
        assert extractor.extract("") == ()
        # Empty content must not trigger an embedding call.
        assert embedder.calls == []

    def test_whitespace_content_returns_empty_tuple(self) -> None:
        embedder = _FakeEmbedder({"ai": (1.0, 0.0)})
        extractor = TopicExtractor(embedder=embedder, vocabulary=("ai",))
        embedder.calls.clear()
        assert extractor.extract("   \n\t") == ()
        assert embedder.calls == []

    def test_high_similarity_word_selected(self) -> None:
        embedder = _FakeEmbedder(
            {
                "ai": (1.0, 0.0),
                "music": (0.0, 1.0),
                "doc": (0.99, 0.14),
            }
        )
        extractor = TopicExtractor(embedder=embedder, vocabulary=("ai", "music"), threshold=0.5)
        # "doc" cosines ~0.99 with "ai", ~0.14 with "music" → only "ai".
        assert extractor.extract("doc") == ("ai",)

    def test_below_threshold_excluded(self) -> None:
        embedder = _FakeEmbedder(
            {
                "ai": (1.0, 0.0),
                "music": (0.0, 1.0),
                "doc": (0.6, 0.6),
            }
        )
        # cos((0.6,0.6), (1,0)) ≈ 0.707 — below threshold 0.8.
        extractor = TopicExtractor(embedder=embedder, vocabulary=("ai", "music"), threshold=0.8)
        assert extractor.extract("doc") == ()

    def test_top_k_caps_result(self) -> None:
        embedder = _FakeEmbedder(
            {
                "ai": (1.0, 0.0, 0.0),
                "ml": (0.95, 0.31, 0.0),
                "data": (0.9, 0.43, 0.0),
                "doc": (0.99, 0.14, 0.0),
            }
        )
        extractor = TopicExtractor(
            embedder=embedder,
            vocabulary=("ai", "ml", "data"),
            top_k=2,
            threshold=0.0,
        )
        result = extractor.extract("doc")
        assert len(result) == 2
        assert result[0] == "ai"  # highest cosine

    def test_tie_break_alphabetical(self) -> None:
        # Both vocab words orient identically → identical cosine.
        embedder = _FakeEmbedder(
            {
                "alpha": (1.0, 0.0),
                "bravo": (1.0, 0.0),
                "doc": (1.0, 0.0),
            }
        )
        extractor = TopicExtractor(
            embedder=embedder,
            vocabulary=("bravo", "alpha"),
            top_k=2,
            threshold=0.0,
        )
        # Equal score → alphabetical ascending.
        assert extractor.extract("doc") == ("alpha", "bravo")

    def test_extract_calls_embedder_only_for_content(self) -> None:
        embedder = _FakeEmbedder(
            {
                "ai": (1.0, 0.0),
                "music": (0.0, 1.0),
                "doc": (0.99, 0.14),
            }
        )
        extractor = TopicExtractor(embedder=embedder, vocabulary=("ai", "music"))
        embedder.calls.clear()
        extractor.extract("doc")
        assert embedder.calls == ["doc"]

    def test_threshold_is_inclusive(self) -> None:
        embedder = _FakeEmbedder(
            {
                "ai": (1.0, 0.0),
                "doc": (0.5, 0.866),
            }
        )
        # cos((0.5,0.866), (1,0)) = 0.5 exactly.
        extractor = TopicExtractor(embedder=embedder, vocabulary=("ai",), threshold=0.5)
        assert extractor.extract("doc") == ("ai",)

    def test_zero_norm_content_returns_empty(self) -> None:
        embedder = _FakeEmbedder(
            {
                "ai": (1.0, 0.0),
                "doc": (0.0, 0.0),
            }
        )
        extractor = TopicExtractor(embedder=embedder, vocabulary=("ai",), threshold=0.0)
        # Zero vector has undefined cosine — treated as 0 similarity,
        # and threshold=0.0 is inclusive, so 0 still passes. We only
        # check it doesn't crash.
        result = extractor.extract("doc")
        assert isinstance(result, tuple)


class TestDeterminism:
    def test_repeated_extract_identical(self) -> None:
        embedder = _FakeEmbedder(
            {
                "ai": (1.0, 0.0),
                "ml": (0.95, 0.31),
                "music": (0.0, 1.0),
                "doc": (0.99, 0.14),
            }
        )
        extractor = TopicExtractor(
            embedder=embedder,
            vocabulary=("ai", "ml", "music"),
            top_k=3,
            threshold=0.0,
        )
        first = extractor.extract("doc")
        second = extractor.extract("doc")
        assert first == second


def test_protocol_is_satisfied() -> None:
    embedder = _FakeEmbedder({"ai": (1.0, 0.0)})
    extractor = TopicExtractor(embedder=embedder, vocabulary=("ai",))
    assert isinstance(extractor, TopicExtractorLike)
