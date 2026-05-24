"""TDD spec for ``litemiro.embedding.sentence_transformers.STEmbedder``.

Real ``sentence-transformers`` weights are heavy and the project's
unit gate must stay sub-second; the suite stubs ``_load_model`` so
behaviour is verified without ever reaching into the optional extras.
A separate ``@pytest.mark.integration`` test is intended for W3
end-to-end validation when CI provisions the model cache.
"""

from __future__ import annotations

from typing import Any

from litemiro.embedding.sentence_transformers import STEmbedder
from litemiro.interfaces import EmbedderLike


class _FakeVector:
    """Mimics the ``.tolist()`` slice of ``numpy.ndarray`` we rely on."""

    def __init__(self, values: list[float]) -> None:
        self._values = values

    def tolist(self) -> list[float]:
        return list(self._values)


class _FakeModel:
    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors
        self.encode_calls: list[tuple[str, bool]] = []

    def encode(self, text: str, normalize_embeddings: bool = False) -> _FakeVector:
        self.encode_calls.append((text, normalize_embeddings))
        if text not in self._vectors:
            raise KeyError(f"no fixture vector for {text!r}")
        return _FakeVector(self._vectors[text])


def _patch_loader(embedder: STEmbedder, model: _FakeModel) -> list[int]:
    calls: list[int] = []

    def fake_load() -> Any:
        calls.append(1)
        return model

    embedder._load_model = fake_load  # type: ignore[method-assign]
    return calls


class TestConstruction:
    def test_default_model_name(self) -> None:
        assert STEmbedder().model_name == "all-MiniLM-L6-v2"

    def test_custom_model_name(self) -> None:
        assert STEmbedder(model_name="custom/model").model_name == "custom/model"

    def test_construction_does_not_load_model(self) -> None:
        # Pointing at a deliberately-missing model name would blow up
        # if construction tried to load weights — the fact that this
        # returns is the assertion.
        STEmbedder(model_name="this/does/not/exist")


class TestEmbed:
    def test_returns_tuple_of_floats(self) -> None:
        embedder = STEmbedder()
        model = _FakeModel({"hello": [0.1, 0.2, 0.3]})
        _patch_loader(embedder, model)
        result = embedder.embed("hello")
        assert result == (0.1, 0.2, 0.3)
        assert isinstance(result, tuple)
        assert all(isinstance(v, float) for v in result)

    def test_normalize_embeddings_passed_to_model(self) -> None:
        embedder = STEmbedder()
        model = _FakeModel({"hi": [1.0, 0.0]})
        _patch_loader(embedder, model)
        embedder.embed("hi")
        # The contract relies on cosine downstream, so normalisation
        # must be on for the production adapter.
        assert model.encode_calls == [("hi", True)]

    def test_model_loaded_lazily_on_first_embed(self) -> None:
        embedder = STEmbedder()
        model = _FakeModel({"x": [1.0]})
        loads = _patch_loader(embedder, model)
        assert loads == []  # construction did NOT load
        embedder.embed("x")
        assert loads == [1]  # first embed loaded once

    def test_model_loaded_only_once_across_calls(self) -> None:
        embedder = STEmbedder()
        model = _FakeModel({"x": [1.0], "y": [0.0]})
        loads = _patch_loader(embedder, model)
        embedder.embed("x")
        embedder.embed("y")
        embedder.embed("x")
        assert loads == [1]

    def test_each_embed_call_invokes_model_encode(self) -> None:
        embedder = STEmbedder()
        model = _FakeModel({"a": [1.0], "b": [0.0]})
        _patch_loader(embedder, model)
        embedder.embed("a")
        embedder.embed("b")
        assert [call[0] for call in model.encode_calls] == ["a", "b"]

    def test_integer_values_coerced_to_float(self) -> None:
        embedder = STEmbedder()
        # Some encoders return numpy ints — make sure tolist→tuple
        # output stays float-typed.
        model = _FakeModel({"x": [1, 0, -1]})
        _patch_loader(embedder, model)
        result = embedder.embed("x")
        assert result == (1.0, 0.0, -1.0)
        assert all(isinstance(v, float) for v in result)


def test_protocol_is_satisfied() -> None:
    assert isinstance(STEmbedder(), EmbedderLike)
