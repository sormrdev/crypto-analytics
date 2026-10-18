from datetime import UTC, datetime
from decimal import Decimal

import httpx
import pytest

from crypto_analytics.config import Settings
from crypto_analytics.domain import Timeframe
from crypto_analytics.kraken import KrakenAPIError, KrakenClient

OHLC_RESULT = {
    "XXBTZUSD": [
        [1750154400, "104000.1", "104500.0", "103900.0", "104200.5", "104150.2", "12.5", 340],
        [1750155300, "104200.5", "104300.0", "104100.0", "104250.0", "104210.0", "3.25", 81],
    ],
    "last": 1750155300,
}


def make_client(handler, **overrides) -> tuple[KrakenClient, list[float]]:
    sleeps: list[float] = []
    settings = Settings(request_interval_s=0, max_retries=3, **overrides)
    http = httpx.Client(base_url=settings.kraken_base_url, transport=httpx.MockTransport(handler))
    return KrakenClient(settings, http=http, sleep=sleeps.append), sleeps


def ok(result):
    return httpx.Response(200, json={"error": [], "result": result})


def test_ohlc_parses_rows_with_decimal_precision():
    seen = {}

    def handler(request: httpx.Request):
        seen.update(request.url.params)
        return ok(OHLC_RESULT)

    client, _ = make_client(handler)
    candles = client.ohlc("XBTUSD", Timeframe.M15, since=datetime(2025, 6, 17, tzinfo=UTC))

    assert seen == {"pair": "XBTUSD", "interval": "15", "since": "1750118400"}
    assert candles[0].open == Decimal("104000.1")
    assert candles[0].vwap == Decimal("104150.2")
    assert candles[0].volume == Decimal("12.5")
    assert candles[1].open_time == datetime.fromtimestamp(1750155300, tz=UTC)


def test_ohlc_skips_zero_timestamp_placeholders():
    result = {"NEWUSD": [[0, "0", "0", "0", "0", "0", "0", 0], *OHLC_RESULT["XXBTZUSD"]], "last": 1750155300}
    client, _ = make_client(lambda request: ok(result))
    assert [c.open_time.year for c in client.ohlc("NEWUSD", Timeframe.M15)] == [2025, 2025]


def test_retries_rate_limit_then_succeeds():
    responses = iter([
        httpx.Response(200, json={"error": ["EAPI:Rate limit exceeded"]}),
        httpx.Response(502),
        ok(OHLC_RESULT),
    ])
    client, sleeps = make_client(lambda request: next(responses))
    assert len(client.ohlc("XBTUSD", Timeframe.M15)) == 2
    assert sleeps == [2, 4]


def test_non_retryable_error_is_raised_immediately():
    client, sleeps = make_client(lambda request: httpx.Response(200, json={"error": ["EQuery:Unknown asset pair"]}))
    with pytest.raises(KrakenAPIError, match="Unknown asset pair"):
        client.ohlc("NOPEUSD", Timeframe.M15)
    assert sleeps == []


def test_gives_up_after_max_retries():
    client, _ = make_client(lambda request: httpx.Response(503))
    with pytest.raises(KrakenAPIError, match="after 3 attempts"):
        client.ohlc("XBTUSD", Timeframe.M15)


def test_trades_return_next_cursor():
    result = {"XXBTZUSD": [["104000.0", "0.5", 1750154401.123, "b", "m", "", 1]], "last": "1750154401123000000"}
    client, _ = make_client(lambda request: ok(result))
    trades, cursor = client.trades("XBTUSD", "0")
    assert trades[0].price == Decimal("104000.0") and trades[0].volume == Decimal("0.5")
    assert cursor == "1750154401123000000"


def test_usd_pairs_filters_quote_and_status():
    result = {
        "XXBTZUSD": {"altname": "XBTUSD", "quote": "ZUSD", "status": "online"},
        "XETHZEUR": {"altname": "ETHEUR", "quote": "ZEUR", "status": "online"},
        "OLDUSD": {"altname": "OLDUSD", "quote": "ZUSD", "status": "delisted"},
    }
    client, _ = make_client(lambda request: ok(result))
    assert client.usd_pairs() == {"XXBTZUSD": "XBTUSD"}


def test_usd_pair_activity_joins_ticker_to_usd_pairs():
    pairs = {"XXBTZUSD": {"altname": "XBTUSD", "quote": "ZUSD"}, "XETHZEUR": {"altname": "ETHEUR", "quote": "ZEUR"}}
    ticker = {
        "XXBTZUSD": {"v": ["1", "2.5"], "p": ["100", "80000"], "l": ["1", "79000"], "h": ["1", "81000"]},
        "XETHZEUR": {"v": ["1", "9"], "p": ["1", "3000"], "l": ["1", "2900"], "h": ["1", "3100"]},
    }
    client, _ = make_client(lambda request: ok(pairs if request.url.path.endswith("/AssetPairs") else ticker))
    [btc] = client.usd_pair_activity()
    assert btc.symbol == "XBTUSD"
    assert btc.turnover_usd == Decimal("200000")  # last-24h volume x last-24h VWAP
    assert (btc.low, btc.high) == (Decimal("79000"), Decimal("81000"))
