from dealcheck.analysis import Label, analyze, compute_stats, daily_series
from dealcheck.models import Snapshot

from .conftest import NOW, steps


def verdict(history, price, **kw):
    target = kw.pop("target", None)
    return analyze(Snapshot(price=price, **kw), history, target=target, now=NOW)


def test_daily_series_forward_fills():
    series = daily_series(steps((5, 100.0), (2, 80.0)), days=180, now=NOW)
    assert [v for _, v in series] == [100.0, 100.0, 100.0, 80.0, 80.0, 80.0]


def test_stats_are_time_weighted():
    # 170 days at 100 €, a 2-day spike to 200 €: the median must stay at 100 €
    history = steps((200, 100.0), (40, 200.0), (38, 100.0))
    stats = compute_stats(history, 100.0, now=NOW)
    assert stats.median == 100.0
    assert stats.max == 200.0
    assert 99 < stats.mean < 102


def test_all_time_low_is_top_deal():
    v = verdict(steps((200, 329.0), (100, 299.0), (90, 329.0), (1, 249.0)), 249.0)
    assert v.label is Label.TOP_DEAL
    assert v.is_deal
    assert round(v.savings_vs_median) == 24


def test_clearly_below_median_is_good_deal():
    v = verdict(steps((200, 399.0), (120, 349.0), (110, 399.0), (1, 359.0)), 359.0)
    assert v.label is Label.GOOD_DEAL


def test_price_hike_before_black_friday_is_fake():
    history = steps((200, 899.0), (25, 1099.0), (1, 899.0))
    v = verdict(history, 899.0, list_price=1199.0)
    assert v.label is Label.FAKE_DEAL
    assert "angehoben" in v.reasons[0]


def test_strike_price_without_real_discount_is_fake():
    v = verdict(steps((200, 549.0), (10, 579.0)), 579.0, list_price=649.0)
    assert v.label is Label.FAKE_DEAL
    assert "−11 %" in v.reasons[0]


def test_normal_and_overpriced():
    history = steps((200, 449.0), (90, 429.0), (84, 449.0))
    assert verdict(history, 449.0).label is Label.NORMAL
    assert verdict(history, 489.0).label is Label.OVERPRICED


def test_not_enough_history():
    v = verdict(steps((3, 100.0)), 90.0, lowest_30d=95.0)
    assert v.label is Label.UNKNOWN
    assert any("Tage Verlauf" in r for r in v.reasons)
    assert any("30 Tage" in r for r in v.reasons)


def test_target_and_omnibus_hints():
    v = verdict(steps((200, 300.0)), 280.0, target=285.0, lowest_30d=260.0)
    assert v.target_hit
    assert any("Wunschpreis" in r for r in v.reasons)
    assert any("260,00 €" in r for r in v.reasons)


def test_unavailable():
    v = verdict(steps((200, 300.0)), None)
    assert v.label is Label.UNKNOWN and v.price is None


def test_out_of_stock_days_are_ignored():
    history = steps((200, 100.0), (100, None), (50, 100.0))
    stats = compute_stats(history, 100.0, now=NOW)
    assert stats.min == 100.0 and stats.median == 100.0
