"""Builds OHLCV + VWAP candles from a time-ordered stream of trades."""

from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from crypto_analytics.domain import Candle, Timeframe, Trade


@dataclass(slots=True)
class _Bucket:
    open_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    notional: Decimal  # sum(price * volume), for VWAP
    trades: int

    def to_candle(self) -> Candle:
        vwap = self.notional / self.volume if self.volume else None
        return Candle(self.open_time, self.open, self.high, self.low, self.close, self.volume, vwap, self.trades)


class CandleAggregator:
    """Feed trades in chronological order; completed candles are yielded as soon as a bucket closes.

    Buckets without trades produce no candle (Kraken's own OHLC behaves the same way).
    """

    def __init__(self, timeframe: Timeframe) -> None:
        self.timeframe = timeframe
        self._bucket: _Bucket | None = None

    def add(self, trades: Iterable[Trade]) -> Iterator[Candle]:
        for trade in trades:
            start = self.timeframe.floor(trade.time)
            bucket = self._bucket
            if bucket is not None and start < bucket.open_time:
                raise ValueError(f"Trade at {trade.time} is older than the current bucket {bucket.open_time}")
            if bucket is None or start != bucket.open_time:
                if bucket is not None:
                    yield bucket.to_candle()
                self._bucket = _Bucket(start, trade.price, trade.price, trade.price, trade.price,
                                       trade.volume, trade.price * trade.volume, 1)
                continue
            bucket.high = max(bucket.high, trade.price)
            bucket.low = min(bucket.low, trade.price)
            bucket.close = trade.price
            bucket.volume += trade.volume
            bucket.notional += trade.price * trade.volume
            bucket.trades += 1

    def pending(self) -> Candle | None:
        """The bucket still being built (may be incomplete)."""
        return self._bucket.to_candle() if self._bucket else None
