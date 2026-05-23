"""Unit spec for ``litemiro._vector.cosine``.

``cosine`` is the shared similarity primitive behind ``FeedEngine``'s
semantic candidacy and ``TopicExtractor``; both suites cover it only
*indirectly*. This file pins its own contract: scale invariance, the
zero-norm short-circuit, and the ``zip(strict=True)`` length guard.
"""

from __future__ import annotations

import math

import pytest

from litemiro._vector import cosine


class TestCosine:
    def test_identical_vectors_score_one(self) -> None:
        assert cosine((1.0, 2.0, 3.0), (1.0, 2.0, 3.0)) == pytest.approx(1.0)

    def test_orthogonal_vectors_score_zero(self) -> None:
        assert cosine((1.0, 0.0), (0.0, 1.0)) == pytest.approx(0.0)

    def test_opposite_vectors_score_minus_one(self) -> None:
        assert cosine((1.0, 0.0), (-1.0, 0.0)) == pytest.approx(-1.0)

    def test_sixty_degrees_scores_one_half(self) -> None:
        # unit vectors 60° apart -> cos 60° = 0.5
        assert cosine((1.0, 0.0), (0.5, math.sqrt(3.0) / 2.0)) == pytest.approx(0.5)

    def test_is_scale_invariant(self) -> None:
        # cosine measures angle, not magnitude
        assert cosine((1.0, 1.0), (5.0, 5.0)) == pytest.approx(1.0)

    def test_zero_left_operand_scores_zero(self) -> None:
        assert cosine((0.0, 0.0), (1.0, 1.0)) == 0.0

    def test_zero_right_operand_scores_zero(self) -> None:
        assert cosine((1.0, 1.0), (0.0, 0.0)) == 0.0

    def test_both_zero_operands_score_zero(self) -> None:
        assert cosine((0.0, 0.0), (0.0, 0.0)) == 0.0

    def test_empty_vectors_score_zero(self) -> None:
        # empty -> norm 0 -> the zero-norm guard short-circuits
        assert cosine((), ()) == 0.0

    def test_mismatched_length_raises(self) -> None:
        # both operands have non-zero norm, so evaluation reaches
        # ``zip(strict=True)``, which rejects the length mismatch
        with pytest.raises(ValueError, match="argument"):
            cosine((1.0, 2.0), (3.0,))

    def test_zero_norm_guard_precedes_length_check(self) -> None:
        # a zero-norm operand returns 0.0 before the length mismatch is
        # ever evaluated — documents the ordering inside ``cosine``
        assert cosine((), (1.0, 2.0)) == 0.0
