"""Synthetic but realistic price histories, so the dashboard can be tried without any setup."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from . import config, report
from .analysis import analyze
from .models import PricePoint, Snapshot
from .storage import Store

# name, normal price, (days_ago, price) events, current price, list price, target
SCENARIOS = [
    (
        "Noise-Cancelling Kopfhörer",
        329.0,
        [(150, 299.0), (140, 329.0), (60, 309.0), (45, 329.0), (3, 249.0)],
        None,
        379.0,
        260.0,
    ),
    (
        '55" OLED-Fernseher',
        899.0,
        [(120, 879.0), (100, 899.0), (28, 1099.0), (2, 899.0)],
        None,
        1199.0,
        None,
    ),
    (
        "Saugroboter mit Wischfunktion",
        399.0,
        [(130, 349.0), (118, 399.0), (70, 369.0), (62, 399.0), (5, 359.0)],
        None,
        None,
        None,
    ),
    (
        "Spielkonsole (Digital Edition)",
        449.0,
        [(90, 429.0), (84, 449.0), (20, 459.0), (12, 449.0)],
        None,
        None,
        None,
    ),
    (
        "Siebträger-Espressomaschine",
        549.0,
        [(160, 579.0), (110, 549.0), (35, 599.0), (14, 579.0)],
        None,
        649.0,
        None,
    ),
]


def build_history(normal: float, events, days: int = 180, seed: int = 0) -> list[PricePoint]:
    rnd = random.Random(seed)
    now = datetime.now(timezone.utc).replace(hour=9, minute=0, second=0, microsecond=0)
    points = [PricePoint(now - timedelta(days=days + 5), normal, "demo")]
    for ago, price in events:
        points.append(PricePoint(now - timedelta(days=ago, hours=rnd.randint(0, 8)), price, "demo"))
    # a few small, short-lived wobbles like real marketplace prices
    for _ in range(4):
        ago = rnd.randint(20, days - 10)
        if any(abs(ago - e[0]) < 6 for e in events):
            continue
        t = now - timedelta(days=ago)
        points += [
            PricePoint(t, round(normal * rnd.uniform(0.97, 1.03), 2), "demo"),
            PricePoint(t + timedelta(days=rnd.randint(1, 3)), normal, "demo"),
        ]
    return sorted(points)


def create(out: str | Path = "demo") -> Path:
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    products = []
    for i, (name, *_rest) in enumerate(SCENARIOS):
        target = SCENARIOS[i][5]
        entry = {"url": f"https://example.com/demo/{i + 1}", "name": name}
        if target:
            entry["target"] = target
        products.append(entry)
    wl_path = out / "watchlist.yaml"
    wl_path.write_text(yaml.safe_dump({"products": products}, allow_unicode=True), encoding="utf-8")

    wl = config.load(wl_path)
    store = Store(wl.settings.data_dir)
    state = {}
    for seed, (product, scenario) in enumerate(zip(wl.products, SCENARIOS, strict=True)):
        _, normal, events, _, list_price, _ = scenario
        history = build_history(normal, events, seed=seed)
        store.add(product.id, history)
        current = history[-1].price
        snap = Snapshot(price=current, title=product.name, list_price=list_price)
        verdict = analyze(snap, store.history(product.id), product.target)
        s = verdict.stats
        state[product.id] = {
            "name": product.name,
            "url": product.url,
            "currency": "EUR",
            "price": current,
            "list_price": list_price if list_price and list_price > current else None,
            "target": product.target,
            "target_hit": verdict.target_hit,
            "label": verdict.label.value,
            "reasons": verdict.reasons,
            "median": s.median if s else None,
            "min": s.min if s else None,
            "max": s.max if s else None,
            "days": s.days if s else 0,
            "checked_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        }
    store.save_state(state)
    return report.build(wl)
