"""Parsing of human-written prices such as ``1.299,99 €`` or ``$1,299.99``."""

from __future__ import annotations

import re

_NUMBER = re.compile(r"\d[\d.,\s  ']*")


def parse_price(text: str | float | int | None) -> float | None:
    """Parse a price in German or English notation. Returns ``None`` if nothing usable is found.

    >>> parse_price("1.299,99 €")
    1299.99
    >>> parse_price("$1,299.99")
    1299.99
    >>> parse_price("19,-")
    19.0
    """
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text) if text >= 0 else None

    match = _NUMBER.search(str(text))
    if not match:
        return None
    raw = re.sub(r"[\s  ']", "", match.group(0)).rstrip(".,")
    if not raw:
        return None

    has_dot, has_comma = "." in raw, "," in raw
    if has_dot and has_comma:
        decimal = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal == "," else ","
        raw = raw.replace(thousands, "").replace(decimal, ".")
    elif has_comma:
        head, _, tail = raw.rpartition(",")
        # "1,299" is a thousands separator, "12,99" / "12,9" is a decimal comma
        if len(tail) == 3 and raw.count(",") >= 1 and head:
            raw = raw.replace(",", "")
        else:
            raw = head.replace(",", "") + "." + tail
    elif has_dot:
        head, _, tail = raw.rpartition(".")
        # German thousands separator: "1.299" or "1.299.000"
        if len(tail) == 3 and (raw.count(".") > 1 or len(head) <= 3):
            raw = raw.replace(".", "")
        else:
            raw = head.replace(".", "") + "." + tail
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if value >= 0 else None


def fmt_price(value: float | None, currency: str = "EUR") -> str:
    if value is None:
        return "–"
    symbol = {"EUR": "€", "USD": "$", "GBP": "£"}.get(currency, currency)
    text = f"{value:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{text} {symbol}" if symbol == "€" else f"{symbol}{value:,.2f}"
