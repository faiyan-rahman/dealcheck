"""Deal analysis: is today's price actually good compared to the last months?

Price histories are step functions (a price holds until it changes), so all statistics are
computed on a *daily* series: for every day we take the price that was valid at the end of
that day. Averaging the raw change events instead would over-weight short price spikes.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from enum import Enum

from .models import PricePoint, Snapshot
from .prices import fmt_price


class Label(str, Enum):
    TOP_DEAL = "top_deal"
    GOOD_DEAL = "good_deal"
    NORMAL = "normal"
    OVERPRICED = "overpriced"
    FAKE_DEAL = "fake_deal"
    UNKNOWN = "unknown"

    @property
    def title(self) -> str:
        return {
            "top_deal": "Top-Deal",
            "good_deal": "Guter Deal",
            "normal": "Normalpreis",
            "overpriced": "Zu teuer",
            "fake_deal": "Fake-Rabatt",
            "unknown": "Zu wenig Daten",
        }[self.value]

    @property
    def emoji(self) -> str:
        return {
            "top_deal": "🔥",
            "good_deal": "✅",
            "normal": "➖",
            "overpriced": "📈",
            "fake_deal": "🚩",
            "unknown": "❔",
        }[self.value]


@dataclass
class Stats:
    days: int  # days of history covered
    min: float
    max: float
    median: float
    mean: float
    p25: float
    min_30d: float | None
    max_30d: float | None
    percentile: float  # share of days on which the item was cheaper than now (0..100)


@dataclass
class Verdict:
    label: Label
    price: float | None
    stats: Stats | None = None
    reasons: list[str] = field(default_factory=list)
    target_hit: bool = False

    @property
    def savings_vs_median(self) -> float | None:
        if self.price is None or not self.stats:
            return None
        return (self.stats.median - self.price) / self.stats.median * 100

    @property
    def is_deal(self) -> bool:
        return self.label in (Label.TOP_DEAL, Label.GOOD_DEAL)


def daily_series(
    points: list[PricePoint], days: int = 180, now: datetime | None = None
) -> list[tuple[date, float | None]]:
    """Forward-fill a list of price changes into one value per day for the last ``days`` days."""
    now = now or datetime.now(timezone.utc)
    points = sorted(points)
    if not points:
        return []
    start = (now - timedelta(days=days)).date()
    first_day = max(start, points[0].ts.date())
    out: list[tuple[date, float | None]] = []
    idx, current = 0, None
    day = first_day
    while day <= now.date():
        end_of_day = datetime.combine(day, datetime.max.time(), tzinfo=timezone.utc)
        while idx < len(points) and points[idx].ts <= end_of_day:
            current = points[idx].price
            idx += 1
        out.append((day, current))
        day += timedelta(days=1)
    return out


def _quantile(sorted_values: list[float], q: float) -> float:
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = (len(sorted_values) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(sorted_values) - 1)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def compute_stats(
    points: list[PricePoint], price: float | None, days: int = 180, now: datetime | None = None
) -> Stats | None:
    series = daily_series(points, days, now)
    # Judge today against the past, not against itself.
    past = [(d, v) for d, v in series[:-1] if v is not None]
    if not past:
        return None
    values = sorted(v for _, v in past)
    recent = [v for _, v in past[-30:]]
    cheaper = sum(1 for v in values if price is not None and v < price)
    return Stats(
        days=len(series) - 1,
        min=values[0],
        max=values[-1],
        median=statistics.median(values),
        mean=statistics.fmean(values),
        p25=_quantile(values, 0.25),
        min_30d=min(recent) if recent else None,
        max_30d=max(recent) if recent else None,
        percentile=cheaper / len(values) * 100 if price is not None else 0.0,
    )


MIN_DAYS = 14


def analyze(
    snapshot: Snapshot,
    history: list[PricePoint],
    target: float | None = None,
    days: int = 180,
    now: datetime | None = None,
) -> Verdict:
    price = snapshot.price
    cur = snapshot.currency
    if price is None:
        return Verdict(Label.UNKNOWN, None, reasons=["Aktuell nicht verfügbar"])

    stats = compute_stats(history, price, days, now)
    verdict = Verdict(Label.UNKNOWN, price, stats)
    reasons = verdict.reasons

    if target is not None and price <= target:
        verdict.target_hit = True
        reasons.append(f"Wunschpreis {fmt_price(target, cur)} erreicht")

    # Signal the shop is legally required to show in the EU (Omnibus directive).
    if snapshot.lowest_30d and price > snapshot.lowest_30d * 1.005:
        reasons.append(
            f"Laut Shop war es in den letzten 30 Tagen schon für "
            f"{fmt_price(snapshot.lowest_30d, cur)} zu haben"
        )

    if not stats or stats.days < MIN_DAYS:
        have = stats.days if stats else 0
        reasons.append(
            f"Erst {have} Tage Verlauf – für ein Urteil braucht es mind. {MIN_DAYS} "
            "(Tipp: Keepa-Key für Amazon setzen)"
        )
        if snapshot.lowest_30d and price <= snapshot.lowest_30d:
            reasons.append("Günstigster Preis der letzten 30 Tage laut Shop")
        return verdict

    med = stats.median
    hike = stats.max_30d is not None and stats.max_30d > med * 1.10 and price >= med * 0.97
    claimed_discount = snapshot.list_price and snapshot.list_price > price * 1.05

    # A price that never changed is the "lowest" too, but it is no deal.
    if price <= stats.min * 1.01 and price <= med * 0.97:
        verdict.label = Label.TOP_DEAL
        reasons.insert(0, f"Tiefstpreis der letzten {stats.days} Tage")
    elif price <= med * 0.90 or (price < stats.p25 and price < med * 0.97):
        verdict.label = Label.GOOD_DEAL
        reasons.insert(
            0,
            f"{verdict.savings_vs_median:.0f} % unter dem Median ({fmt_price(med, cur)}); "
            f"nur an {stats.percentile:.0f} % der Tage war es günstiger",
        )
    elif hike:
        verdict.label = Label.FAKE_DEAL
        reasons.insert(
            0,
            f"Preis wurde in den letzten 30 Tagen auf bis zu {fmt_price(stats.max_30d, cur)} "
            f"angehoben – der jetzige „Rabatt“ führt nur zurück zum Normalpreis "
            f"({fmt_price(med, cur)})",
        )
    elif claimed_discount and price >= med * 0.97:
        verdict.label = Label.FAKE_DEAL
        shown = (1 - price / snapshot.list_price) * 100
        reasons.insert(
            0,
            f"Shop wirbt mit −{shown:.0f} % (statt {fmt_price(snapshot.list_price, cur)}), "
            f"aber der 6-Monats-Median liegt bei {fmt_price(med, cur)}",
        )
    elif price >= med * 1.05:
        verdict.label = Label.OVERPRICED
        reasons.insert(0, f"{-verdict.savings_vs_median:.0f} % über dem Median ({fmt_price(med, cur)})")
    else:
        verdict.label = Label.NORMAL
        reasons.insert(0, f"Liegt im üblichen Bereich (Median {fmt_price(med, cur)})")

    if verdict.label is not Label.TOP_DEAL and price > stats.min:
        reasons.append(f"Tiefstpreis im Zeitraum: {fmt_price(stats.min, cur)}")
    return verdict
