"""Watchlist (``watchlist.yaml``) loading and editing."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import yaml

from .models import Product

ASIN_RE = re.compile(r"/(?:dp|gp/product|gp/aw/d|exec/obidos/ASIN|o/ASIN)/([A-Z0-9]{10})(?:[/?#]|$)")


def extract_asin(url: str) -> str | None:
    """Return the Amazon ASIN of a product URL, if it is an Amazon URL."""
    host = urlparse(url).netloc.lower()
    if "amazon." not in host and "amzn." not in host:
        return None
    match = ASIN_RE.search(urlparse(url).path + "/")
    return match.group(1) if match else None


def amazon_domain(url: str) -> str:
    """``https://www.amazon.de/...`` -> ``de``; ``amazon.co.uk`` -> ``co.uk``."""
    host = urlparse(url).netloc.lower()
    _, _, tld = host.partition("amazon.")
    return tld or "de"


def make_id(url: str, name: str | None = None) -> str:
    asin = extract_asin(url)
    if asin:
        return asin.lower()
    base = name or urlparse(url).path.strip("/").split("/")[-1] or urlparse(url).netloc
    slug = re.sub(r"[^a-z0-9]+", "-", base.lower()).strip("-")[:40] or "product"
    digest = hashlib.sha1(url.encode()).hexdigest()[:6]
    return f"{slug}-{digest}"


@dataclass
class Settings:
    history_days: int = 180
    currency: str = "EUR"
    data_dir: Path = Path("data")
    site_dir: Path = Path("site")
    keepa_key: str | None = None
    ntfy_topic: str | None = None
    ntfy_server: str = "https://ntfy.sh"
    telegram_token: str | None = None
    telegram_chat_id: str | None = None


@dataclass
class Watchlist:
    path: Path
    settings: Settings
    products: list[Product] = field(default_factory=list)

    def find(self, key: str) -> Product | None:
        key = key.lower()
        for product in self.products:
            if key in (product.id.lower(), (product.asin or "").lower(), product.url.lower()):
                return product
        return None

    def add(self, url: str, name: str | None = None, target: float | None = None) -> Product:
        product = Product(id=make_id(url, name), url=url, name=name, target=target, asin=extract_asin(url))
        if self.find(product.id):
            raise ValueError(f"{product.id} is already on the watchlist")
        self.products.append(product)
        return product

    def remove(self, key: str) -> Product:
        product = self.find(key)
        if not product:
            raise KeyError(key)
        self.products.remove(product)
        return product

    def save(self) -> None:
        raw: dict = {}
        if self.path.exists():
            raw = yaml.safe_load(self.path.read_text(encoding="utf-8")) or {}
        raw["products"] = [
            {k: v for k, v in (("url", p.url), ("name", p.name), ("target", p.target)) if v}
            for p in self.products
        ]
        self.path.write_text(yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")


def load(path: str | Path = "watchlist.yaml") -> Watchlist:
    """Load the watchlist. Secrets are read from environment variables, never from the file."""
    path = Path(path)
    raw = {}
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}

    cfg = raw.get("settings") or {}
    notify = raw.get("notify") or {}
    base = path.parent
    settings = Settings(
        history_days=int(cfg.get("history_days", 180)),
        currency=cfg.get("currency", "EUR"),
        data_dir=base / cfg.get("data_dir", "data"),
        site_dir=base / cfg.get("site_dir", "site"),
        keepa_key=os.getenv("KEEPA_API_KEY") or None,
        ntfy_topic=os.getenv("DEALCHECK_NTFY_TOPIC") or notify.get("ntfy_topic"),
        ntfy_server=notify.get("ntfy_server", "https://ntfy.sh"),
        telegram_token=os.getenv("DEALCHECK_TELEGRAM_TOKEN") or None,
        telegram_chat_id=os.getenv("DEALCHECK_TELEGRAM_CHAT_ID") or notify.get("telegram_chat_id"),
    )

    products = []
    for item in raw.get("products") or []:
        if isinstance(item, str):
            item = {"url": item}
        url = item["url"].strip()
        target = item.get("target")
        products.append(
            Product(
                id=item.get("id") or make_id(url, item.get("name")),
                url=url,
                name=item.get("name"),
                target=float(target) if target is not None else None,
                asin=extract_asin(url),
            )
        )
    return Watchlist(path=path, settings=settings, products=products)
