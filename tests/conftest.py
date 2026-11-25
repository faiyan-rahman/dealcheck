from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dealcheck.models import PricePoint

FIXTURES = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 11, 27, 12, 0, tzinfo=timezone.utc)  # Black Friday 2026


@pytest.fixture
def fixture_html():
    return lambda name: (FIXTURES / name).read_text(encoding="utf-8")


def steps(*events: tuple[int, float | None], now: datetime = NOW) -> list[PricePoint]:
    """Build a price history from ``(days_ago, price)`` change events."""
    return sorted(PricePoint(now - timedelta(days=ago), price, "test") for ago, price in events)
