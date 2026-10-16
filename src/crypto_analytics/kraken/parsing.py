"""Pure functions turning raw Kraken JSON payloads into domain objects (no I/O, easy to test)."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from crypto_analytics.domain import Candle, Trade


def extract_series(result: dict[str, Any]) -> list[list[Any]]:
    """Kraken keys the data by its internal pair name (e.g. XXBTZUSD), next to a `last` cursor."""
    keys = [k for k in result if k != "last"]
    if len(keys) != 1:
        raise ValueError(f"Expected exactly one pair in Kraken result, got {keys}")
    return result[keys[0]]


def parse_ohlc(result: dict[str, Any]) -> list[Candle]:
    # Row layout: [time, open, high, low, close, vwap, volume, count].
    # Rows with time 0 are placeholders Kraken can return for freshly listed pairs – skip them.
    return [
        Candle(
            open_time=datetime.fromtimestamp(int(row[0]), tz=UTC),
            open=Decimal(row[1]),
            high=Decimal(row[2]),
            low=Decimal(row[3]),
            close=Decimal(row[4]),
            vwap=Decimal(row[5]),
            volume=Decimal(row[6]),
            trade_count=int(row[7]),
        )
        for row in extract_series(result)
        if int(row[0]) > 0
    ]


def parse_trades(result: dict[str, Any]) -> list[Trade]:
    # Row layout: [price, volume, time, side, order_type, misc, trade_id]
    return [
        Trade(
            time=datetime.fromtimestamp(float(row[2]), tz=UTC),
            price=Decimal(row[0]),
            volume=Decimal(row[1]),
        )
        for row in extract_series(result)
    ]


def parse_usd_pairs(result: dict[str, Any], quote: str) -> list[str]:
    """Altnames (e.g. XBTUSD) of tradable pairs quoted in `quote`."""
    return sorted(
        info["altname"]
        for info in result.values()
        if info.get("quote") == quote and info.get("status", "online") == "online"
    )
