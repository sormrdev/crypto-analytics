from datetime import UTC, datetime
from decimal import Decimal

from crypto_analytics.domain import Candle, PairActivity
from crypto_analytics.services.sync import crypto_by_24h_turnover, rank_pairs, read_whitelist, write_whitelist


def pair(symbol: str, turnover: int, vwap: str = "50", low: str = "45", high: str = "55") -> PairActivity:
    return PairActivity(symbol, Decimal(turnover), Decimal(vwap), Decimal(low), Decimal(high))


def daily(volume: str, vwap: str) -> Candle:
    price = Decimal(vwap)
    return Candle(datetime(2026, 10, 1, tzinfo=UTC), price, price, price, price, Decimal(volume), price)


class FakeClient:
    def __init__(self, activity: list[PairActivity], candles: dict[str, list[Candle]]) -> None:
        self.activity, self.candles, self.ohlc_calls = activity, candles, []

    def usd_pair_activity(self) -> list[PairActivity]:
        return self.activity

    def ohlc(self, symbol, timeframe, since):
        self.ohlc_calls.append(symbol)
        return self.candles.get(symbol, [])


def test_skips_fiat_gold_and_stablecoins():
    activity = [
        pair("ETHUSD", 40), pair("XBTUSD", 60),
        pair("USDTUSD", 50, vwap="1.0001", low="0.9998", high="1.0003"),  # pegged to $1
        pair("EURUSD", 30, vwap="1.16", low="1.15", high="1.17"),
        pair("PAXGUSD", 20),
        pair("ADAUSD", 10, vwap="0.99", low="0.95", high="1.04"),  # near $1, but it moves
    ]
    ranked = crypto_by_24h_turnover(activity, {"EUR", "PAXG"})
    assert [p.symbol for p in ranked] == ["XBTUSD", "ETHUSD", "ADAUSD"]


def test_ranks_a_shortlist_by_turnover_over_the_window():
    activity = [pair(f"P{i}USD", 100 - i) for i in range(10)]  # P0 busiest in the last 24h
    # Over 30 days P2 traded the most: 3 days x 10 x 50 USD.
    candles = {"P0USD": [daily("1", "100")], "P1USD": [daily("2", "100")], "P2USD": [daily("10", "50")] * 3}
    client = FakeClient(activity, candles)
    assert rank_pairs(client, 2, set()) == [("P2USD", Decimal("1500")), ("P1USD", Decimal("200"))]
    assert client.ohlc_calls == ["P0USD", "P1USD", "P2USD", "P3USD", "P4USD", "P5USD"]  # 3 x top only


def test_whitelist_file_round_trips_and_ignores_comments(tmp_path):
    path = tmp_path / "tracked_pairs.txt"
    write_whitelist(path, [("XBTUSD", Decimal("1000000")), ("ETHUSD", Decimal("500"))], days=30)
    assert read_whitelist(path) == ["XBTUSD", "ETHUSD"]
