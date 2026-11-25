"""Command line interface: ``dealcheck add|list|remove|check|watch|report|demo``."""

from __future__ import annotations

import argparse
import logging
import sys
import time
import webbrowser
from pathlib import Path

from rich.console import Console
from rich.table import Table

from . import __version__, config, notify, report, tracker
from .analysis import Label
from .prices import fmt_price
from .storage import Store

console = Console()

STYLE = {
    Label.TOP_DEAL: "bold green",
    Label.GOOD_DEAL: "green",
    Label.NORMAL: "dim",
    Label.OVERPRICED: "yellow",
    Label.FAKE_DEAL: "bold red",
    Label.UNKNOWN: "dim",
}


def _print_result(r: tracker.CheckResult) -> None:
    name = r.product.name or (r.snapshot.title if r.snapshot else None) or r.product.id
    if not r.verdict:
        console.print(f"[red]✗[/] {name}: " + "; ".join(r.errors))
        return
    v = r.verdict
    cur = r.snapshot.currency
    bell = " 🔔" if r.notified else ""
    console.print(
        f"{v.label.emoji} [{STYLE[v.label]}]{v.label.title:<14}[/] "
        f"[bold]{fmt_price(v.price, cur):>12}[/]  {name[:70]}{bell}"
    )
    for reason in v.reasons:
        console.print(f"     [dim]•[/] {reason}")
    for err in r.errors:
        console.print(f"     [yellow]! {err}[/]")


def cmd_add(args) -> int:
    wl = config.load(args.watchlist)
    try:
        product = wl.add(args.url, args.name, args.target)
    except ValueError as exc:
        console.print(f"[yellow]{exc}[/]")
        return 1
    wl.save()
    console.print(f"[green]+[/] {product.label} [dim]({product.id})[/]")
    if product.asin and not wl.settings.keepa_key:
        console.print("[dim]Tipp: Mit KEEPA_API_KEY gibt es sofort den 6-Monats-Verlauf für Amazon.[/]")
    return 0


def cmd_remove(args) -> int:
    wl = config.load(args.watchlist)
    try:
        product = wl.remove(args.id)
    except KeyError:
        console.print(f"[red]Nicht gefunden:[/] {args.id}")
        return 1
    wl.save()
    console.print(f"[red]−[/] {product.label}")
    return 0


def cmd_list(args) -> int:
    wl = config.load(args.watchlist)
    state = Store(wl.settings.data_dir).state()
    table = Table(box=None, header_style="dim")
    for col in ("ID", "Produkt", "Preis", "Urteil", "Ziel", "Geprüft"):
        table.add_column(col)
    for p in wl.products:
        st = state.get(p.id, {})
        label = Label(st.get("label", "unknown"))
        table.add_row(
            p.id,
            (p.name or st.get("title") or p.url)[:50],
            fmt_price(st.get("price"), st.get("currency", "EUR")),
            f"[{STYLE[label]}]{label.emoji} {label.title}[/]",
            fmt_price(p.target) if p.target else "",
            (st.get("checked_at") or "")[:16].replace("T", " "),
        )
    console.print(table if wl.products else "[dim]Watchlist ist leer.[/]")
    return 0


def _check(args) -> list[tracker.CheckResult]:
    wl = config.load(args.watchlist)
    if not wl.products:
        console.print("[dim]Watchlist ist leer – füge mit `dealcheck add URL` etwas hinzu.[/]")
        return []
    notifiers = [] if args.no_notify else notify.from_settings(wl.settings)
    source = "Keepa + Shop" if wl.settings.keepa_key else "Shop-Seiten"
    channels = ", ".join(n.name for n in notifiers) or "keine"
    console.print(f"[dim]Prüfe {len(wl.products)} Produkt(e) · Quelle: {source} · Push: {channels}[/]\n")
    results = tracker.run(wl, notifiers, only=args.id, on_result=_print_result)
    if not args.no_report:
        path = report.build(wl)
        console.print(f"\n[dim]Dashboard: {path}[/]")
    return results


def cmd_check(args) -> int:
    results = _check(args)
    return 0 if all(r.verdict for r in results) else 2


def cmd_watch(args) -> int:
    console.print(f"[bold]dealcheck watch[/] – alle {args.interval} min, Strg+C beendet\n")
    try:
        while True:
            _check(args)
            console.rule(f"[dim]nächster Check in {args.interval} min[/]")
            time.sleep(args.interval * 60)
    except KeyboardInterrupt:
        return 0


def cmd_report(args) -> int:
    wl = config.load(args.watchlist)
    path = report.build(wl, args.out)
    console.print(f"Dashboard: {path}")
    if args.open:
        webbrowser.open(path.resolve().as_uri())
    return 0


def cmd_demo(args) -> int:
    from . import demo

    path = demo.create(args.out)
    console.print(f"Demo-Dashboard: {path}")
    if args.open:
        webbrowser.open(Path(path).resolve().as_uri())
    return 0


def cmd_test_notify(args) -> int:
    wl = config.load(args.watchlist)
    notifiers = notify.from_settings(wl.settings)
    if not notifiers:
        console.print("[yellow]Kein Kanal konfiguriert (DEALCHECK_NTFY_TOPIC oder Telegram).[/]")
        return 1
    errors = notify.broadcast(
        notifiers, notify.Message("dealcheck ✓", "Push-Benachrichtigungen funktionieren.")
    )
    for e in errors:
        console.print(f"[red]{e}[/]")
    return 1 if errors else 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="dealcheck",
        description="Erkennt echte Deals und Fake-Rabatte anhand des Preisverlaufs.",
    )
    p.add_argument("--version", action="version", version=f"dealcheck {__version__}")
    p.add_argument("-w", "--watchlist", default="watchlist.yaml", help="Pfad zur Watchlist")
    p.add_argument("-v", "--verbose", action="store_true")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("add", help="Produkt-URL zur Watchlist hinzufügen")
    s.add_argument("url")
    s.add_argument("-n", "--name")
    s.add_argument("-t", "--target", type=float, help="Wunschpreis")
    s.set_defaults(func=cmd_add)

    s = sub.add_parser("remove", help="Produkt entfernen")
    s.add_argument("id")
    s.set_defaults(func=cmd_remove)

    s = sub.add_parser("list", help="Watchlist mit letztem Stand anzeigen")
    s.set_defaults(func=cmd_list)

    for name, func, helptext in (
        ("check", cmd_check, "Alle Preise einmal prüfen"),
        ("watch", cmd_watch, "Dauerhaft im Intervall prüfen"),
    ):
        s = sub.add_parser(name, help=helptext)
        s.add_argument("--id", help="nur dieses Produkt")
        s.add_argument("--no-notify", action="store_true", help="keine Push-Nachrichten")
        s.add_argument("--no-report", action="store_true", help="kein Dashboard schreiben")
        if name == "watch":
            s.add_argument("-i", "--interval", type=int, default=120, help="Minuten (Standard 120)")
        s.set_defaults(func=func)

    s = sub.add_parser("report", help="HTML-Dashboard erzeugen")
    s.add_argument("-o", "--out")
    s.add_argument("--open", action="store_true", help="im Browser öffnen")
    s.set_defaults(func=cmd_report)

    s = sub.add_parser("demo", help="Demo-Dashboard mit Beispieldaten erzeugen")
    s.add_argument("-o", "--out", default="demo")
    s.add_argument("--open", action="store_true")
    s.set_defaults(func=cmd_demo)

    s = sub.add_parser("test-notify", help="Test-Push senden")
    s.set_defaults(func=cmd_test_notify)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING, format="%(levelname)s %(message)s"
    )
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8")  # emoji on Windows consoles
        except (ValueError, OSError):
            pass
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
