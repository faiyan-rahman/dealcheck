"""Generic shop-page scraper.

Works on most shops without per-shop code by reading structured data first
(schema.org JSON-LD, microdata, OpenGraph), then falls back to Amazon's markup.
It also picks up two signals German shops have to show by law (EU Omnibus directive,
§ 11 PAngV): the strike-through price and the lowest price of the last 30 days.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..models import PricePoint, Snapshot
from ..prices import parse_price

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.7",
}

AMAZON_PRICE = [
    "#corePriceDisplay_desktop_feature_div .priceToPay .a-offscreen",
    "#corePrice_feature_div .priceToPay .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-price .a-offscreen",
    "#corePrice_feature_div .a-price .a-offscreen",
    "#apex_desktop .a-price .a-offscreen",
    "#priceblock_dealprice",
    "#priceblock_ourprice",
    "#price_inside_buybox",
]
AMAZON_LIST_PRICE = [
    "#corePriceDisplay_desktop_feature_div .basisPrice .a-offscreen",
    "#corePriceDisplay_desktop_feature_div .a-text-price .a-offscreen",
    "#corePrice_feature_div .a-text-price .a-offscreen",
    ".basisPrice .a-offscreen",
]

_PRICE_TOKEN = r"(\d{1,3}(?:[.\s ]\d{3})*(?:,\d{1,2})?|\d+(?:[.,]\d{1,2})?)"
LOWEST_30D = [
    re.compile(
        r"niedrigste[rn]?\s+(?:gesamt)?preis[^\d€]{0,60}?30\s*tage[^\d€]{0,25}?" + _PRICE_TOKEN,
        re.I,
    ),
    re.compile(r"lowest\s+price[^\d€$£]{0,60}?30\s*days[^\d]{0,25}?" + _PRICE_TOKEN, re.I),
    re.compile(r"30[-\s]*tage[-\s]*bestpreis[^\d€]{0,25}?" + _PRICE_TOKEN, re.I),
]
LIST_PRICE_TEXT = re.compile(
    r"(?:uvp|statt|unverbindliche\s+preisempfehlung|durchschnittspreis|vorher)"
    r"[:\s]{0,10}(?:€\s*)?" + _PRICE_TOKEN,
    re.I,
)


class FetchError(RuntimeError):
    pass


class BlockedError(FetchError):
    """The shop answered with a captcha / bot wall."""


def fetch_page(url: str, session: requests.Session | None = None, timeout: float = 20) -> str:
    http = session or requests.Session()
    try:
        resp = http.get(url, headers=HEADERS, timeout=timeout)
    except requests.RequestException as exc:
        raise FetchError(f"request failed: {exc}") from exc
    if resp.status_code in (403, 429, 503):
        raise BlockedError(f"HTTP {resp.status_code} – the shop is blocking automated requests")
    if resp.status_code >= 400:
        raise FetchError(f"HTTP {resp.status_code}")
    text = resp.text
    if "/errors/validateCaptcha" in text or "api-services-support@amazon.com" in text:
        raise BlockedError("Amazon shows a captcha – use a Keepa key or retry later")
    return text


def _walk(node: Any):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from _walk(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk(item)


def _types(node: dict) -> set[str]:
    t = node.get("@type", [])
    return {x.lower() for x in (t if isinstance(t, list) else [t]) if isinstance(x, str)}


def _from_json_ld(soup: BeautifulSoup) -> dict[str, Any]:
    found: dict[str, Any] = {}
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except (json.JSONDecodeError, TypeError):
            continue
        for node in _walk(data):
            types = _types(node)
            if "product" in types and "title" not in found and isinstance(node.get("name"), str):
                found["title"] = node["name"].strip()
            if types & {"offer", "aggregateoffer"} and "price" not in found:
                price = node.get("price", node.get("lowPrice"))
                spec = node.get("priceSpecification")
                if price is None and isinstance(spec, dict):
                    price = spec.get("price")
                if price is None and isinstance(spec, list):
                    for s in spec:
                        if isinstance(s, dict) and "strike" not in str(s.get("priceType", "")).lower():
                            price = s.get("price")
                            break
                if isinstance(price, str) and re.fullmatch(r"\s*\d+(\.\d+)?\s*", price):
                    value = float(price)  # machine format: "1299.00" is never 1.299.000
                else:
                    value = parse_price(price)
                if value is not None:
                    found["price"] = value
                    found["currency"] = node.get("priceCurrency") or (
                        spec.get("priceCurrency") if isinstance(spec, dict) else None
                    )
                    avail = str(node.get("availability", "")).lower()
                    found["in_stock"] = not any(s in avail for s in ("outofstock", "soldout", "discontinued"))
                for s in spec if isinstance(spec, list) else []:
                    if isinstance(s, dict) and "strike" in str(s.get("priceType", "")).lower():
                        found["list_price"] = parse_price(s.get("price"))
    return found


def _meta(soup: BeautifulSoup, *names: str) -> str | None:
    for name in names:
        tag = (
            soup.find("meta", attrs={"property": name})
            or soup.find("meta", attrs={"itemprop": name})
            or soup.find("meta", attrs={"name": name})
        )
        if tag and tag.get("content"):
            return tag["content"]
    return None


def _select_price(soup: BeautifulSoup, selectors: list[str]) -> float | None:
    for selector in selectors:
        for tag in soup.select(selector):
            value = parse_price(tag.get_text(" ", strip=True))
            if value:
                return value
    return None


def parse_page(html: str, url: str = "") -> Snapshot:
    """Extract a :class:`Snapshot` from a product page."""
    soup = BeautifulSoup(html, "html.parser")
    data = _from_json_ld(soup)
    is_amazon = "amazon." in url

    price = data.get("price")
    if price is None and is_amazon:
        price = _select_price(soup, AMAZON_PRICE)
    if price is None:
        tag = soup.find(attrs={"itemprop": "price"})
        if tag is not None:
            price = parse_price(tag.get("content") or tag.get_text(" ", strip=True))
    if price is None:
        price = parse_price(_meta(soup, "product:price:amount", "og:price:amount"))

    title = data.get("title")
    if not title and is_amazon and soup.select_one("#productTitle"):
        title = soup.select_one("#productTitle").get_text(" ", strip=True)
    if not title:
        title = _meta(soup, "og:title") or (soup.title.get_text(strip=True) if soup.title else None)

    currency = (
        data.get("currency")
        or _meta(soup, "product:price:currency", "og:price:currency", "priceCurrency")
        or "EUR"
    )

    text = soup.get_text(" ", strip=True)
    list_price = data.get("list_price")
    if list_price is None and is_amazon:
        list_price = _select_price(soup, AMAZON_LIST_PRICE)
    if list_price is None:
        m = LIST_PRICE_TEXT.search(text)
        if m:
            list_price = parse_price(m.group(1))
    if list_price is not None and price is not None and list_price <= price:
        list_price = None  # not a discount

    lowest_30d = None
    for pattern in LOWEST_30D:
        m = pattern.search(text)
        if m:
            lowest_30d = parse_price(m.group(1))
            break

    in_stock = data.get("in_stock", True)
    if is_amazon and re.search(r"derzeit nicht verfügbar|currently unavailable", text, re.I):
        in_stock = False

    now = datetime.now(timezone.utc)
    snap = Snapshot(
        price=price if in_stock else None,
        currency=currency,
        title=title,
        list_price=list_price,
        lowest_30d=lowest_30d,
        in_stock=in_stock and price is not None,
        source="scrape",
        fetched_at=now,
    )
    if price is not None or not in_stock:
        snap.history = [PricePoint(now, snap.price, "scrape")]
    return snap
