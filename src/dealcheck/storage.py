"""Plain-file storage: one CSV per product plus a small JSON state file.

Plain files keep the history diffable in git, which is what lets the GitHub Actions
tracker commit new prices back to the repository.
"""

from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .models import PricePoint

FIELDS = ["timestamp", "price", "source"]


class Store:
    def __init__(self, data_dir: str | Path):
        self.root = Path(data_dir)
        self.prices_dir = self.root / "prices"
        self.state_file = self.root / "state.json"

    def _csv(self, product_id: str) -> Path:
        return self.prices_dir / f"{product_id}.csv"

    def history(self, product_id: str) -> list[PricePoint]:
        path = self._csv(product_id)
        if not path.exists():
            return []
        points = []
        with path.open(newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                ts = datetime.fromisoformat(row["timestamp"])
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                price = float(row["price"]) if row["price"] not in ("", "None") else None
                points.append(PricePoint(ts, price, row.get("source") or "scrape"))
        return sorted(points)

    def add(self, product_id: str, points: list[PricePoint]) -> int:
        """Merge points into the product's history. Returns the number of new points."""
        existing = self.history(product_id)
        seen = {(p.ts.replace(microsecond=0), p.source) for p in existing}
        fresh = [p for p in points if (p.ts.replace(microsecond=0), p.source) not in seen]
        if not fresh:
            return 0
        merged = sorted(existing + fresh)
        self.prices_dir.mkdir(parents=True, exist_ok=True)
        with self._csv(product_id).open("w", newline="", encoding="utf-8") as fh:
            writer = csv.writer(fh)
            writer.writerow(FIELDS)
            for p in merged:
                writer.writerow(
                    [
                        p.ts.astimezone(timezone.utc).replace(microsecond=0).isoformat(),
                        "" if p.price is None else f"{p.price:.2f}",
                        p.source,
                    ]
                )
        return len(fresh)

    def state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {}
        return json.loads(self.state_file.read_text(encoding="utf-8"))

    def save_state(self, state: dict[str, Any]) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(state, indent=2, ensure_ascii=False, sort_keys=True), encoding="utf-8"
        )
