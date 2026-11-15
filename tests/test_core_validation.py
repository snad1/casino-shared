"""Tests for shared/core/validation.py."""

import pytest

from shared.core.validation import InvalidNumber, finite_float


class TestFiniteFloat:
    @pytest.mark.parametrize("value", ["0", "1.5", "-2", 0, 3, 1e-9])
    def test_accepts_finite_numbers(self, value):
        assert isinstance(finite_float("amount", value), float)

    @pytest.mark.parametrize("value", ["nan", "NaN", "inf", "-inf", float("nan"), float("inf")])
    def test_rejects_non_finite(self, value):
        with pytest.raises(InvalidNumber, match="finite"):
            finite_float("max loss", value)

    @pytest.mark.parametrize("value", ["abc", "", None, [1]])
    def test_rejects_non_numeric(self, value):
        with pytest.raises(InvalidNumber, match="must be a number"):
            finite_float("max loss", value)

    def test_minimum(self):
        with pytest.raises(InvalidNumber, match="at least 0"):
            finite_float("delay", -1, minimum=0)
        assert finite_float("delay", 0, minimum=0) == 0.0

    def test_maximum(self):
        with pytest.raises(InvalidNumber, match="at most 10"):
            finite_float("multiplier", 11, maximum=10)
        assert finite_float("multiplier", 10, maximum=10) == 10.0

    def test_message_names_the_field(self):
        with pytest.raises(InvalidNumber) as exc:
            finite_float("Max loss", "nan")
        assert "Max loss" in str(exc.value)
