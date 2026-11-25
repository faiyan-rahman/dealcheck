import pytest

from dealcheck.prices import fmt_price, parse_price


@pytest.mark.parametrize(
    "text, expected",
    [
        ("249,00 €", 249.0),
        ("1.299,99 €", 1299.99),
        ("1.299 €", 1299.0),
        ("€ 12,9", 12.9),
        ("$1,299.99", 1299.99),
        ("1,299", 1299.0),
        ("19,-", 19.0),
        ("EUR 1 299,00", 1299.0),
        ("1299.00", 1299.0),
        ("12.50", 12.5),
        (79, 79.0),
        (49.95, 49.95),
        ("ab 2.499,00 €", 2499.0),
        ("kostenlos", None),
        ("", None),
        (None, None),
    ],
)
def test_parse_price(text, expected):
    assert parse_price(text) == expected


def test_fmt_price():
    assert fmt_price(1299.5) == "1.299,50 €"
    assert fmt_price(None) == "–"
    assert fmt_price(10, "USD") == "$10.00"
