from datetime import UTC, datetime
from decimal import Decimal

import pytest

from crypto_analytics.aggregation import CandleAggregator
from crypto_analytics.domain import Timeframe, Trade


def trade(hh: int, mm: int, ss: int, price: str, volume: str) -> Trade:
    return Trade(datetime(2026, 6, 17, hh, mm, ss, tzinfo=UTC), Decimal(price), Decimal(volume))


def test_builds_ohlcv_and_vwap_per_bucket():
    agg = CandleAggregator(Timeframe.M15)
    done = list(agg.add([
        trade(10, 0, 5, "100", "1"),
        trade(10, 7, 0, "110", "1"),
        trade(10, 14, 59, "90", "2"),
        trade(10, 15, 0, "95", "4"),  # opens the next bucket and closes the first
    ]))

    assert len(done) == 1
    c = done[0]
    assert c.open_time == datetime(2026, 6, 17, 10, 0, tzinfo=UTC)
    assert (c.open, c.high, c.low, c.close) == (Decimal(100), Decimal(110), Decimal(90), Decimal(90))
    assert c.volume == Decimal(4)
    assert c.vwap == Decimal(390) / Decimal(4)  # (100 + 110 + 180) / 4
    assert c.trade_count == 3

    pending = agg.pending()
    assert pending.open_time == datetime(2026, 6, 17, 10, 15, tzinfo=UTC)
    assert pending.close == Decimal(95)


def test_buckets_without_trades_are_skipped():
    agg = CandleAggregator(Timeframe.M15)
    done = list(agg.add([trade(10, 1, 0, "1", "1"), trade(11, 31, 0, "2", "1")]))
    assert [c.open_time.minute for c in done] == [0]
    assert agg.pending().open_time == datetime(2026, 6, 17, 11, 30, tzinfo=UTC)


def test_state_carries_over_between_pages():
    agg = CandleAggregator(Timeframe.M15)
    assert list(agg.add([trade(10, 0, 0, "100", "1")])) == []
    done = list(agg.add([trade(10, 5, 0, "120", "1"), trade(10, 20, 0, "1", "1")]))
    assert done[0].high == Decimal(120) and done[0].trade_count == 2


def test_rejects_out_of_order_trades():
    agg = CandleAggregator(Timeframe.M15)
    list(agg.add([trade(10, 30, 0, "1", "1")]))
    with pytest.raises(ValueError):
        list(agg.add([trade(10, 0, 0, "1", "1")]))


def test_zero_volume_bucket_has_no_vwap():
    agg = CandleAggregator(Timeframe.M15)
    list(agg.add([trade(10, 0, 0, "100", "0")]))
    assert agg.pending().vwap is None
