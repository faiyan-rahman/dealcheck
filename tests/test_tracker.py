from datetime import datetime, timedelta, timezone

import yaml

from dealcheck import config, report, tracker
from dealcheck.analysis import Label, Verdict
from dealcheck.cli import main
from dealcheck.models import PricePoint
from dealcheck.notify import Message, Notifier
from dealcheck.storage import Store


class Recorder(Notifier):
    name = "recorder"

    def __init__(self):
        self.messages: list[Message] = []

    def send(self, msg):
        self.messages.append(msg)


def make_watchlist(tmp_path, products):
    path = tmp_path / "watchlist.yaml"
    path.write_text(yaml.safe_dump({"products": products}), encoding="utf-8")
    return config.load(path)


def page(price: str) -> str:
    return (
        '<script type="application/ld+json">{"@type":"Product","name":"Kopfhörer",'
        f'"offers":{{"@type":"Offer","price":"{price}","priceCurrency":"EUR"}}}}</script>'
    )


def seed(store: Store, pid: str, price: float, days: int = 120):
    now = datetime.now(timezone.utc)
    store.add(pid, [PricePoint(now - timedelta(days=days), price, "scrape")])


def test_storage_roundtrip_and_dedupe(tmp_path):
    store = Store(tmp_path)
    t = datetime(2026, 11, 1, tzinfo=timezone.utc)
    pts = [PricePoint(t, 10.0, "keepa"), PricePoint(t + timedelta(days=1), None, "keepa")]
    assert store.add("x", pts) == 2
    assert store.add("x", pts) == 0
    assert store.history("x") == pts


def test_run_notifies_once_per_deal(tmp_path):
    wl = make_watchlist(tmp_path, [{"url": "https://shop.example/kh", "name": "KH"}])
    pid = wl.products[0].id
    store = Store(wl.settings.data_dir)
    seed(store, pid, 329.0)
    rec = Recorder()

    results = tracker.run(wl, [rec], fetch=lambda url, s=None: page("249.00"))
    assert results[0].verdict.label is Label.TOP_DEAL
    assert results[0].notified and len(rec.messages) == 1
    assert "Top-Deal" in rec.messages[0].title

    # same price again: no second push
    tracker.run(wl, [rec], fetch=lambda url, s=None: page("249.00"))
    assert len(rec.messages) == 1

    # noticeably cheaper: push again
    tracker.run(wl, [rec], fetch=lambda url, s=None: page("229.00"))
    assert len(rec.messages) == 2

    state = store.state()[pid]
    assert state["label"] == "top_deal" and state["notified_price"] == 229.0


def test_target_price_push_even_without_history(tmp_path):
    wl = make_watchlist(tmp_path, [{"url": "https://shop.example/kh", "name": "KH", "target": 300}])
    rec = Recorder()
    tracker.run(wl, [rec], fetch=lambda url, s=None: page("289.00"))
    assert len(rec.messages) == 1 and "Wunschpreis" in rec.messages[0].title


def test_fetch_error_is_reported_not_raised(tmp_path):
    from dealcheck.fetchers import BlockedError

    def boom(url, s=None):
        raise BlockedError("captcha")

    wl = make_watchlist(tmp_path, [{"url": "https://shop.example/kh"}])
    results = tracker.run(wl, [], fetch=boom)
    assert results[0].verdict is None
    assert "captcha" in results[0].errors[0]


def test_should_notify_rules():
    v = Verdict(Label.NORMAL, 100.0)
    assert not tracker.should_notify({}, v)
    v.target_hit = True
    assert tracker.should_notify({}, v)
    assert not tracker.should_notify({"target_hit": True}, v)


def test_report_renders(tmp_path):
    wl = make_watchlist(tmp_path, [{"url": "https://shop.example/kh", "name": "KH <b>"}])
    seed(Store(wl.settings.data_dir), wl.products[0].id, 329.0)
    tracker.run(wl, [], fetch=lambda url, s=None: page("249.00"))
    html = report.build(wl).read_text(encoding="utf-8")
    assert "Top-Deal" in html and "<svg" in html
    assert "KH &lt;b&gt;" in html  # escaped


def test_cli_add_list_remove(tmp_path, capsys):
    wl = str(tmp_path / "watchlist.yaml")
    assert main(["-w", wl, "add", "https://www.amazon.de/dp/B09XS7JWHH", "-t", "250"]) == 0
    assert main(["-w", wl, "add", "https://www.amazon.de/dp/B09XS7JWHH"]) == 1
    loaded = config.load(wl)
    assert loaded.products[0].asin == "B09XS7JWHH" and loaded.products[0].target == 250
    assert main(["-w", wl, "list"]) == 0
    assert main(["-w", wl, "remove", "b09xs7jwhh"]) == 0
    assert config.load(wl).products == []


def test_demo(tmp_path):
    from dealcheck import demo

    path = demo.create(tmp_path / "demo")
    html = path.read_text(encoding="utf-8")
    for label in ("Top-Deal", "Guter Deal", "Fake-Rabatt", "Normalpreis"):
        assert label in html
