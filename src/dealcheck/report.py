"""Static HTML dashboard (one self-contained file, no JavaScript framework, works on GitHub Pages)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from html import escape
from pathlib import Path

from .analysis import Label, daily_series
from .config import Watchlist
from .prices import fmt_price
from .storage import Store

W, H, PAD_L, PAD_R, PAD_T, PAD_B = 560, 170, 8, 64, 12, 22
MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun", "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]


def svg_chart(
    series: list[tuple[date, float | None]],
    median: float | None = None,
    target: float | None = None,
    currency: str = "EUR",
) -> str:
    values = [v for _, v in series if v is not None]
    if len(series) < 2 or not values:
        return '<div class="nochart">Noch kein Verlauf – kommt mit den nächsten Checks.</div>'

    lo, hi = min(values), max(values)
    refs = [x for x in (median, target) if x is not None]
    lo, hi = min([lo, *refs]), max([hi, *refs])
    span = (hi - lo) or max(hi * 0.1, 1)
    lo, hi = lo - span * 0.12, hi + span * 0.12
    n = len(series)
    iw, ih = W - PAD_L - PAD_R, H - PAD_T - PAD_B

    def x(i: float) -> float:
        return PAD_L + i / (n - 1) * iw

    def y(v: float) -> float:
        return PAD_T + (hi - v) / (hi - lo) * ih

    # step line, broken where the item was unavailable
    segments: list[list[str]] = []
    prev = None
    for i, (_, v) in enumerate(series):
        if v is None:
            prev = None
            continue
        if prev is None:
            segments.append([f"M{x(i):.1f},{y(v):.1f}"])
        else:
            segments[-1].append(f"H{x(i):.1f}V{y(v):.1f}")
        prev = v
    line = " ".join("".join(s) for s in segments)
    area = " ".join("".join(s) + f"V{PAD_T + ih:.1f}H{float(s[0][1:].split(',')[0]):.1f}Z" for s in segments)

    parts = [
        f'<svg viewBox="0 0 {W} {H}" class="chart" role="img" aria-label="Preisverlauf der letzten {n} Tage">'
    ]
    # month gridlines
    for i, (d, _) in enumerate(series):
        if d.day == 1 and 0 < i < n - 1:
            parts.append(
                f'<line x1="{x(i):.1f}" x2="{x(i):.1f}" y1="{PAD_T}" y2="{PAD_T + ih}" class="gl"/>'
                f'<text x="{x(i) + 3:.1f}" y="{H - 6}" class="axis">{MONTHS[d.month - 1]}</text>'
            )
    parts.append(f'<path d="{area}" class="area"/>')
    labels: list[tuple[float, str, str]] = []  # right-hand axis labels, first wins on overlap
    if target is not None:
        parts.append(
            f'<line x1="{PAD_L}" x2="{PAD_L + iw}" y1="{y(target):.1f}" y2="{y(target):.1f}" class="target"/>'
        )
        labels.append((y(target), "Ziel", "ref target-t"))
    if median is not None:
        parts.append(
            f'<line x1="{PAD_L}" x2="{PAD_L + iw}" y1="{y(median):.1f}" y2="{y(median):.1f}" class="median"/>'
        )
        labels.append((y(median), "Median", "ref"))
    parts.append(f'<path d="{line}" class="line"/>')

    vmin, vmax = min(values), max(values)
    labels += [(y(vmax), fmt_price(vmax, currency), "axis"), (y(vmin), fmt_price(vmin, currency), "axis")]
    placed: list[float] = []
    for ly, text, cls in labels:
        if any(abs(ly - other) < 12 for other in placed):
            continue
        placed.append(ly)
        parts.append(f'<text x="{PAD_L + iw + 6}" y="{ly + 4:.1f}" class="{cls}">{escape(text)}</text>')
    last_i = max(i for i, (_, v) in enumerate(series) if v is not None)
    parts.append(f'<circle cx="{x(last_i):.1f}" cy="{y(series[last_i][1]):.1f}" r="4" class="now"/>')
    # hover targets with native tooltips
    step = iw / (n - 1)
    for i, (d, v) in enumerate(series):
        tip = f"{d:%d.%m.%Y}: {fmt_price(v, currency) if v is not None else 'nicht verfügbar'}"
        parts.append(
            f'<rect x="{x(i) - step / 2:.1f}" y="{PAD_T}" width="{step:.2f}" height="{ih}" '
            f'class="hit"><title>{escape(tip)}</title></rect>'
        )
    parts.append("</svg>")
    return "".join(parts)


def _card(pid: str, st: dict, series, product_name: str | None, target: float | None) -> str:
    label = Label(st.get("label", "unknown"))
    cur = st.get("currency", "EUR")
    name = product_name or st.get("title") or pid
    price, median = st.get("price"), st.get("median")
    delta = ""
    if price is not None and median:
        diff = (price - median) / median * 100
        cls = "down" if diff < -0.5 else "up" if diff > 0.5 else "flat"
        delta = f'<span class="delta {cls}">{diff:+.0f} % vs. Median</span>'
    strike = (
        f'<s title="Streich-/UVP-Preis des Shops">{fmt_price(st["list_price"], cur)}</s>'
        if st.get("list_price")
        else ""
    )
    stats = [
        ("Tief", st.get("min")),
        ("Median", median),
        ("Hoch", st.get("max")),
        ("Ziel", target),
    ]
    stat_html = "".join(
        f"<div><dt>{k}</dt><dd>{fmt_price(v, cur)}</dd></div>" for k, v in stats if v is not None
    )
    reasons = "".join(f"<li>{escape(r)}</li>" for r in st.get("reasons", []))
    errors = "".join(f"<li>{escape(e)}</li>" for e in st.get("errors", []))
    days = st.get("days") or 0
    checked = st.get("checked_at", "")[:16].replace("T", " ")
    return f"""
<article class="card {label.value}">
  <header>
    <span class="badge">{label.emoji} {label.title}</span>
    <span class="meta">{days} Tage Verlauf</span>
  </header>
  <h2><a href="{escape(st.get("url", "#"))}" target="_blank" rel="noopener">{escape(name)}</a></h2>
  <div class="price"><strong>{fmt_price(price, cur)}</strong>{strike}{delta}</div>
  {svg_chart(series, median, target, cur)}
  <dl class="stats">{stat_html}</dl>
  <ul class="reasons">{reasons}</ul>
  {f'<ul class="errors">{errors}</ul>' if errors else ""}
  <footer>Geprüft {escape(checked)} UTC</footer>
</article>"""


ORDER = ["top_deal", "good_deal", "fake_deal", "normal", "overpriced", "unknown"]


def build(watchlist: Watchlist, out_dir: str | Path | None = None) -> Path:
    settings = watchlist.settings
    store = Store(settings.data_dir)
    state = store.state()
    out = Path(out_dir or settings.site_dir)
    out.mkdir(parents=True, exist_ok=True)

    cards, counts = [], {k: 0 for k in ORDER}
    products = sorted(
        watchlist.products,
        key=lambda p: ORDER.index(state.get(p.id, {}).get("label", "unknown")),
    )
    for product in products:
        st = state.get(product.id) or {"url": product.url}
        st.setdefault("url", product.url)
        counts[st.get("label", "unknown")] += 1
        series = daily_series(store.history(product.id), settings.history_days)
        cards.append(_card(product.id, st, series, product.name, product.target))

    chips = "".join(
        f'<span class="chip {k}">{Label(k).emoji} {Label(k).title}: {v}</span>'
        for k, v in counts.items()
        if v
    )
    now = datetime.now(timezone.utc).strftime("%d.%m.%Y %H:%M")
    html = TEMPLATE.format(
        updated=now,
        chips=chips,
        cards="".join(cards) or '<p class="empty">Watchlist ist leer – <code>dealcheck add URL</code></p>',
    )
    path = out / "index.html"
    path.write_text(html, encoding="utf-8")
    return path


TEMPLATE = """<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>dealcheck – echte Deals oder Fake-Rabatt?</title>
<style>
:root {{
  --bg: #f6f5f1; --card: #ffffff; --ink: #1d1d1b; --muted: #6f6e69; --line: #e6e4dd;
  --accent: #2f5bd3; --top: #0b8a4c; --good: #2f9e5b; --fake: #d23c2c; --over: #c76a12;
  --norm: #7a7a74; --area: rgba(47, 91, 211, .08);
}}
@media (prefers-color-scheme: dark) {{
  :root {{
    --bg: #121212; --card: #1c1c1c; --ink: #ecebe6; --muted: #9a9993; --line: #2c2c2a;
    --accent: #7c9dff; --top: #3ccf86; --good: #5fc489; --fake: #ff6b5b; --over: #f0a050;
    --norm: #a3a39c; --area: rgba(124, 157, 255, .10);
  }}
}}
* {{ box-sizing: border-box; }}
body {{ margin: 0; background: var(--bg); color: var(--ink);
  font: 15px/1.5 ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 1240px; margin: 0 auto; padding: 32px 20px 64px; }}
.top {{ display: flex; flex-wrap: wrap; align-items: baseline; gap: 12px 24px; margin-bottom: 8px; }}
h1 {{ font-size: 28px; letter-spacing: -.02em; margin: 0; }}
h1 span {{ color: var(--muted); font-weight: 400; }}
.updated {{ color: var(--muted); font-size: 13px; }}
.chips {{ display: flex; flex-wrap: wrap; gap: 8px; margin: 16px 0 24px; }}
.chip {{ font-size: 13px; padding: 4px 10px; border-radius: 999px; background: var(--card);
  border: 1px solid var(--line); }}
.grid {{ display: grid; grid-template-columns: repeat(auto-fill, minmax(360px, 1fr)); gap: 18px; }}
.card {{ background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  padding: 18px 18px 14px; display: flex; flex-direction: column; gap: 8px;
  border-top: 4px solid var(--c, var(--norm)); }}
.card.top_deal {{ --c: var(--top); }} .card.good_deal {{ --c: var(--good); }}
.card.fake_deal {{ --c: var(--fake); }} .card.overpriced {{ --c: var(--over); }}
.card header {{ display: flex; justify-content: space-between; align-items: center; }}
.badge {{ font-weight: 600; font-size: 13px; color: var(--c, var(--norm)); }}
.meta, footer {{ color: var(--muted); font-size: 12px; }}
h2 {{ font-size: 16px; margin: 0; line-height: 1.35; }}
h2 a {{ color: inherit; text-decoration: none; }} h2 a:hover {{ text-decoration: underline; }}
.price {{ display: flex; align-items: baseline; gap: 10px; flex-wrap: wrap; }}
.price strong {{ font-size: 26px; letter-spacing: -.02em; font-variant-numeric: tabular-nums; }}
.price s {{ color: var(--muted); }}
.delta {{ font-size: 13px; font-weight: 600; }}
.delta.down {{ color: var(--good); }} .delta.up {{ color: var(--fake); }}
.delta.flat {{ color: var(--muted); }}
.chart {{ width: 100%; height: auto; display: block; }}
.chart .line {{ fill: none; stroke: var(--accent); stroke-width: 2; stroke-linejoin: round; }}
.chart .area {{ fill: var(--area); }}
.chart .gl {{ stroke: var(--line); }}
.chart .median {{ stroke: var(--muted); stroke-dasharray: 4 4; }}
.chart .target {{ stroke: var(--good); stroke-dasharray: 2 3; }}
.chart .axis, .chart .ref {{ fill: var(--muted); font-size: 11px; }}
.chart .target-t {{ fill: var(--good); }}
.chart .now {{ fill: var(--c, var(--accent)); stroke: var(--card); stroke-width: 2; }}
.chart .hit {{ fill: transparent; }} .chart .hit:hover {{ fill: rgba(127,127,127,.12); }}
.nochart {{ color: var(--muted); font-size: 13px; padding: 28px 0; text-align: center;
  border: 1px dashed var(--line); border-radius: 10px; }}
.stats {{ display: flex; gap: 18px; margin: 2px 0; }}
.stats div {{ display: flex; flex-direction: column; }}
.stats dt {{ color: var(--muted); font-size: 11px; text-transform: uppercase; letter-spacing: .06em; }}
.stats dd {{ margin: 0; font-variant-numeric: tabular-nums; font-weight: 500; }}
.reasons, .errors {{ margin: 0; padding-left: 18px; font-size: 13.5px; }}
.errors {{ color: var(--over); font-size: 12px; }}
.empty {{ color: var(--muted); }}
@media (max-width: 420px) {{ .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
  <div class="top">
    <h1>dealcheck <span>· echte Deals oder Fake-Rabatt?</span></h1>
    <span class="updated">Stand: {updated} UTC</span>
  </div>
  <div class="chips">{chips}</div>
  <section class="grid">{cards}</section>
</main>
</body>
</html>
"""
