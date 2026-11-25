from datetime import datetime, timezone

import pytest

from dealcheck.config import amazon_domain, extract_asin, make_id
from dealcheck.fetchers.html import BlockedError, fetch_page, parse_page
from dealcheck.fetchers.keepa import keepa_time, parse_series, snapshot_from_product


def test_amazon_page(fixture_html):
    snap = parse_page(fixture_html("amazon.html"), "https://www.amazon.de/dp/B09XS7JWHH")
    assert snap.price == 249.0
    assert snap.title.startswith("Sony WH-1000XM5")
    assert snap.lowest_30d == 299.0
    assert snap.in_stock
    assert len(snap.history) == 1 and snap.history[0].price == 249.0


def test_json_ld_shop(fixture_html):
    snap = parse_page(fixture_html("shop_jsonld.html"), "https://shop.example/x200")
    assert snap.price == 1299.0  # machine format "1299.00" must not become 1299000
    assert snap.title == "Saugroboter X200 mit Wischfunktion"
    assert snap.list_price == 1599.0
    assert snap.lowest_30d == 1199.0
    assert snap.currency == "EUR"


def test_microdata_shop(fixture_html):
    snap = parse_page(fixture_html("shop_microdata.html"), "https://shop.example/espresso")
    assert snap.price == 579.0
    assert snap.list_price == 649.0
    assert snap.lowest_30d is None


def test_out_of_stock_json_ld():
    html = """<script type="application/ld+json">{"@type":"Product","name":"X",
    "offers":{"@type":"Offer","price":"10.00","availability":"https://schema.org/OutOfStock"}}
    </script>"""
    snap = parse_page(html, "https://shop.example/x")
    assert snap.price is None and not snap.in_stock
    assert snap.history[0].price is None


class FakeResponse:
    def __init__(self, status=200, text=""):
        self.status_code, self.text = status, text


class FakeSession:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


def test_captcha_is_detected():
    session = FakeSession(FakeResponse(200, '<form action="/errors/validateCaptcha">'))
    with pytest.raises(BlockedError):
        fetch_page("https://www.amazon.de/dp/B000000000", session)


def test_http_503_is_blocked():
    with pytest.raises(BlockedError):
        fetch_page("https://x.example", FakeSession(FakeResponse(503)))


@pytest.mark.parametrize(
    "url, asin",
    [
        ("https://www.amazon.de/dp/B09XS7JWHH", "B09XS7JWHH"),
        ("https://www.amazon.de/Sony-WH-1000XM5/dp/B09XS7JWHH/ref=sr_1_1?k=x", "B09XS7JWHH"),
        ("https://www.amazon.co.uk/gp/product/B0BSHF7WHW?th=1", "B0BSHF7WHW"),
        ("https://www.otto.de/p/sony-wh-1000xm5-123/", None),
    ],
)
def test_extract_asin(url, asin):
    assert extract_asin(url) == asin


def test_amazon_domain_and_ids():
    assert amazon_domain("https://www.amazon.co.uk/dp/B0BSHF7WHW") == "co.uk"
    assert make_id("https://www.amazon.de/dp/B09XS7JWHH") == "b09xs7jwhh"
    pid = make_id("https://www.otto.de/p/sony-wh-1000xm5-123/")
    assert pid.startswith("sony-wh-1000xm5-123-")
    assert pid == make_id("https://www.otto.de/p/sony-wh-1000xm5-123/")


def test_keepa_time():
    # Keepa minute 0 is 2011-01-01 00:00 UTC
    assert keepa_time(0) == datetime(2011, 1, 1, tzinfo=timezone.utc)


def _km(dt: datetime) -> int:
    return int(dt.timestamp() // 60) - 21_564_000


def test_keepa_series_and_snapshot():
    now = datetime.now(timezone.utc)
    t0 = _km(now) - 400 * 24 * 60  # older than the window
    t1 = _km(now) - 100 * 24 * 60
    t2 = _km(now) - 10 * 24 * 60
    t3 = _km(now) - 1 * 24 * 60
    amazon = [t0, 32900, t1, -1, t2, 29900, t3, 24900]
    points = parse_series(amazon, "keepa-amazon")
    assert [p.price for p in points] == [329.0, None, 299.0, 249.0]

    snap = snapshot_from_product({"title": "Sony", "csv": [amazon, None]}, days=180)
    assert snap.price == 249.0
    assert snap.title == "Sony"
    # the point from before the window is kept so the first day has a known price
    assert [p.price for p in snap.history] == [329.0, None, 299.0, 249.0]

    only_new = snapshot_from_product({"csv": [None, [t2, 19999]]}, days=180, domain="com")
    assert only_new.price == 199.99 and only_new.currency == "USD"
