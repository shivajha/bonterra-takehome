"""Tests for NFR-1 and REQ-2 (AC-14, AC-15 at the unit level)."""
import pytest

from cb.money import MoneyError, format_cents, parse_amount


@pytest.mark.parametrize(
    "text, cents",
    [
        ("300.00", 30000),
        ("300", 30000),
        ("300.5", 30050),
        ("0.01", 1),
        ("0.1", 10),
        ("100000.00", 10000000),
        (" 12.34 ", 1234),
    ],
)
def test_parse_valid_amounts(text, cents):
    assert parse_amount(text) == cents
    assert isinstance(parse_amount(text), int)


@pytest.mark.parametrize(
    "bad",
    ["0", "0.00", "-5", "1.005", "abc", "", "1e3", "1,000", "100000.01", "12.", ".5", "NaN"],
)
def test_parse_rejects_invalid_amounts(bad):  # AC-14
    with pytest.raises(MoneyError):
        parse_amount(bad)


@pytest.mark.parametrize("bad", [300, 300.0, None])
def test_parse_rejects_non_strings(bad):  # floats must never enter the system
    with pytest.raises(MoneyError):
        parse_amount(bad)


def test_no_float_drift():  # AC-15
    assert parse_amount("0.10") + parse_amount("0.20") == parse_amount("0.30")


@pytest.mark.parametrize("cents, text", [(0, "0.00"), (1, "0.01"), (30050, "300.50"), (970000, "9700.00")])
def test_format_cents(cents, text):
    assert format_cents(cents) == text