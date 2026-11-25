from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, order=True)
class PricePoint:
    """A price observation. ``price`` is ``None`` when the item was out of stock."""

    ts: datetime
    price: float | None
    source: str = "scrape"


@dataclass
class Product:
    """An entry of the watchlist."""

    id: str
    url: str
    name: str | None = None
    target: float | None = None
    asin: str | None = None

    @property
    def label(self) -> str:
        return self.name or self.id


@dataclass
class Snapshot:
    """What a shop page (or an API) tells us right now."""

    price: float | None
    currency: str = "EUR"
    title: str | None = None
    list_price: float | None = None  # strike-through / RRP shown by the shop
    lowest_30d: float | None = None  # "lowest price of the last 30 days" (EU Omnibus directive)
    in_stock: bool = True
    source: str = "scrape"
    fetched_at: datetime | None = None
    history: list[PricePoint] = field(default_factory=list)
