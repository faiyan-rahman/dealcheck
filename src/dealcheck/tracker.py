"""The agent loop: fetch → store → analyse → notify, for every product on the watchlist."""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timezone

import requests

from .analysis import Verdict, analyze
from .config import Watchlist, amazon_domain
from .fetchers import FetchError, KeepaClient, KeepaError, fetch_page, parse_page
from .models import Product, Snapshot
from .notify import Message, Notifier, broadcast
from .prices import fmt_price
from .storage import Store

log = logging.getLogger(__name__)


@dataclass
class CheckResult:
    product: Product
    snapshot: Snapshot | None = None
    verdict: Verdict | None = None
    new_points: int = 0
    errors: list[str] = field(default_factory=list)
    notified: bool = False


def gather(
    product: Product,
    days: int,
    store: Store,
    keepa: KeepaClient | None = None,
    session: requests.Session | None = None,
    fetch: Callable[..., str] = fetch_page,
) -> tuple[Snapshot | None, int, list[str]]:
    """Collect the current price (and history, if Keepa is available) for one product."""
    errors: list[str] = []
    new_points = 0
    from_keepa = from_page = None

    if product.asin and keepa:
        try:
            from_keepa = keepa.snapshot(product.asin, amazon_domain(product.url), days)
            new_points += store.add(product.id, from_keepa.history)
        except KeepaError as exc:
            errors.append(f"Keepa: {exc}")

    try:
        from_page = parse_page(fetch(product.url, session), product.url)
        if from_page.history:
            new_points += store.add(product.id, from_page.history)
    except FetchError as exc:
        errors.append(f"Shop: {exc}")

    if from_page and (from_page.price is not None or not from_keepa):
        snap = from_page
        if from_keepa and not snap.title:
            snap.title = from_keepa.title
    else:
        snap = from_keepa
    return snap, new_points, errors


DEAL_LABELS = {"top_deal", "good_deal"}


def should_notify(prev: dict, verdict: Verdict) -> bool:
    if verdict.price is None:
        return False
    if verdict.target_hit and not prev.get("target_hit"):
        return True
    if not verdict.is_deal:
        return False
    if prev.get("label") not in DEAL_LABELS:
        return True
    # Already a deal last time: only ping again if it got noticeably cheaper.
    last = prev.get("notified_price")
    return last is None or verdict.price < last * 0.97


def build_message(product: Product, snap: Snapshot, verdict: Verdict) -> Message:
    name = product.name or snap.title or product.id
    head = f"{verdict.label.emoji} {verdict.label.title}: {fmt_price(verdict.price, snap.currency)}"
    if verdict.target_hit and not verdict.is_deal:
        head = f"🎯 Wunschpreis: {fmt_price(verdict.price, snap.currency)}"
    return Message(
        title=f"{head} – {name[:60]}",
        body="\n".join(f"• {r}" for r in verdict.reasons),
        url=product.url,
        priority=5 if verdict.label.value == "top_deal" else 4,
        tags=("moneybag",) if verdict.is_deal else ("dart",),
    )


def run(
    watchlist: Watchlist,
    notifiers: list[Notifier] | None = None,
    only: str | None = None,
    session: requests.Session | None = None,
    fetch: Callable[..., str] = fetch_page,
    keepa: KeepaClient | None = None,
    on_result: Callable[[CheckResult], None] | None = None,
) -> list[CheckResult]:
    settings = watchlist.settings
    store = Store(settings.data_dir)
    state = store.state()
    session = session or requests.Session()
    if keepa is None and settings.keepa_key:
        keepa = KeepaClient(settings.keepa_key, session)

    results = []
    for product in watchlist.products:
        if only and watchlist.find(only) is not product:
            continue
        result = CheckResult(product)
        snap, result.new_points, result.errors = gather(
            product, settings.history_days, store, keepa, session, fetch
        )
        result.snapshot = snap
        prev = state.get(product.id, {})
        if snap is not None:
            verdict = analyze(snap, store.history(product.id), product.target, settings.history_days)
            result.verdict = verdict
            if notifiers and should_notify(prev, verdict):
                errors = broadcast(notifiers, build_message(product, snap, verdict))
                result.notified = len(errors) < len(notifiers)
                result.errors += errors
            stats = verdict.stats
            state[product.id] = {
                **prev,
                "name": product.name,
                "url": product.url,
                "title": snap.title or prev.get("title"),
                "currency": snap.currency,
                "price": verdict.price,
                "list_price": snap.list_price,
                "lowest_30d": snap.lowest_30d,
                "target": product.target,
                "target_hit": verdict.target_hit,
                "label": verdict.label.value,
                "reasons": verdict.reasons,
                "median": stats.median if stats else None,
                "min": stats.min if stats else None,
                "max": stats.max if stats else None,
                "days": stats.days if stats else 0,
                "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
                "errors": result.errors,
            }
            if result.notified:
                state[product.id]["notified_price"] = verdict.price
        else:
            state.setdefault(product.id, {"url": product.url, "name": product.name})
            state[product.id]["errors"] = result.errors
        results.append(result)
        if on_result:
            on_result(result)

    store.save_state(state)
    if keepa and keepa.tokens_left is not None:
        log.info("Keepa tokens left: %s", keepa.tokens_left)
    return results
