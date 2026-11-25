"""Amazon price history through the Keepa API (https://keepa.com/#!api).

Keepa stores every price change of an Amazon product, so one request gives us
the full 6-month history even for products we only started watching today.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import requests

from ..models import PricePoint, Snapshot

API = "https://api.keepa.com/product"
DOMAINS = {
    "com": 1,
    "co.uk": 2,
    "de": 3,
    "fr": 4,
    "co.jp": 5,
    "ca": 6,
    "it": 8,
    "es": 9,
    "in": 10,
    "com.mx": 11,
    "com.br": 12,
}
# indices into product["csv"]
AMAZON, NEW = 0, 1
KEEPA_EPOCH_MINUTES = 21_564_000


class KeepaError(RuntimeError):
    pass


def keepa_time(minutes: int) -> datetime:
    """Keepa time minutes -> aware UTC datetime."""
    return datetime.fromtimestamp((minutes + KEEPA_EPOCH_MINUTES) * 60, tz=timezone.utc)


def parse_series(csv: list[int] | None, source: str) -> list[PricePoint]:
    """Decode ``[time, price, time, price, ...]``; price in cents, ``-1`` = not available."""
    if not csv:
        return []
    points = []
    for i in range(0, len(csv) - 1, 2):
        t, cents = csv[i], csv[i + 1]
        points.append(PricePoint(keepa_time(t), None if cents < 0 else cents / 100, source))
    return points


class KeepaClient:
    def __init__(self, key: str, session: requests.Session | None = None):
        if not key:
            raise KeepaError("KEEPA_API_KEY is not set")
        self.key = key
        self.http = session or requests.Session()
        self.tokens_left: int | None = None

    def product(self, asin: str, domain: str = "de", days: int = 180) -> dict:
        params = {
            "key": self.key,
            "domain": DOMAINS.get(domain, 3),
            "asin": asin,
            "history": 1,
            "days": days,
        }
        try:
            resp = self.http.get(API, params=params, timeout=30)
        except requests.RequestException as exc:
            raise KeepaError(f"request failed: {exc}") from exc
        try:
            body = resp.json()
        except ValueError as exc:
            raise KeepaError(f"HTTP {resp.status_code}, no JSON") from exc
        self.tokens_left = body.get("tokensLeft")
        if resp.status_code != 200 or body.get("error"):
            message = (body.get("error") or {}).get("message") or f"HTTP {resp.status_code}"
            raise KeepaError(message)
        products = body.get("products") or []
        if not products:
            raise KeepaError(f"Keepa knows no product {asin}")
        return products[0]

    def snapshot(self, asin: str, domain: str = "de", days: int = 180) -> Snapshot:
        product = self.product(asin, domain, days)
        return snapshot_from_product(product, days, domain)


CURRENCIES = {
    "com": "USD",
    "co.uk": "GBP",
    "co.jp": "JPY",
    "ca": "CAD",
    "in": "INR",
    "com.mx": "MXN",
    "com.br": "BRL",
}


def snapshot_from_product(product: dict, days: int = 180, domain: str = "de") -> Snapshot:
    csv = product.get("csv") or []
    amazon = parse_series(csv[AMAZON] if len(csv) > AMAZON else None, "keepa-amazon")
    new = parse_series(csv[NEW] if len(csv) > NEW else None, "keepa-new")
    # Prefer Amazon's own offer; fall back to the cheapest new 3rd-party offer.
    history = amazon if any(p.price for p in amazon) else new
    cutoff = datetime.now(timezone.utc) - timedelta(days=days + 1)
    before = [p for p in history if p.ts < cutoff]
    history = ([before[-1]] if before else []) + [p for p in history if p.ts >= cutoff]

    current = history[-1].price if history else None
    return Snapshot(
        price=current,
        currency=CURRENCIES.get(domain, "EUR"),
        title=product.get("title"),
        in_stock=current is not None,
        source="keepa",
        fetched_at=datetime.now(timezone.utc),
        history=history,
    )
