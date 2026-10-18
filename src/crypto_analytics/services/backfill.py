"""Rebuilds historical candles from raw trades.

Kraken's OHLC endpoint only exposes the last 720 candles, but its Trades endpoint can be paged back to
the very first trade. Aggregating trades locally therefore gives a complete history, including VWAP.
"""

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from crypto_analytics.aggregation import CandleAggregator
from crypto_analytics.db import repository as repo
from crypto_analytics.domain import Candle, Timeframe
from crypto_analytics.kraken import KrakenClient
from crypto_analytics.services.sync import OHLC_WINDOW

log = logging.getLogger(__name__)

FLUSH_EVERY = 2000  # candles held in memory before writing (≈ 3 weeks of 15m data)


@dataclass
class BackfillResult:
    trades: int = 0
    candles_written: int = 0
    last_candle: datetime | None = None


def backfill(client: KrakenClient, sessions: sessionmaker[Session], symbol: str, timeframe: Timeframe,
             start: datetime | None = None, until: datetime | None = None, overwrite: bool = False,
             min_gap: int = 4,
             progress: Callable[[datetime, datetime, datetime, int], None] | None = None) -> BackfillResult:
    """Page through trades from `start` up to `until` (default: now).

    Only stretches of at least `min_gap` missing candles are fetched; candles already stored are skipped
    (unless `overwrite`), since every 1000 trades cost an API call. Without `start`, fills the first hole
    too large for the OHLC endpoint (`ohlc sync` keeps the recent window current, so such holes can only
    appear further back), or else continues after the newest stored candle. Safe to interrupt: everything
    aggregated so far is written before exiting. `progress(start, until, reached, trades)` is called after
    every page of trades."""
    with sessions() as session:
        pair_id = repo.pair_ids(session, [symbol]).get(symbol)
        if pair_id is None:
            raise ValueError(f"{symbol} is not in the pair table - run `crypto pairs sync` first")
        if start is None:
            holes = repo.find_gaps(session, pair_id, timeframe, min_missing=OHLC_WINDOW)
            if holes:
                start, until = holes[0].after, until or holes[0].before
                log.info("%s: filling gap %s -> %s", symbol, start, until)
            else:
                start = repo.latest_candle_time(session, pair_id, timeframe)
            if start is None:
                raise ValueError(f"No {timeframe.code} candles stored for {symbol}; pass a start date")
        # Restart at a bucket boundary: the first bucket is rebuilt in full and overwrites any partial copy.
        start = timeframe.floor(start)
        until = until or datetime.now(UTC)
        ranges = ([(start, until)] if overwrite
                  else repo.missing_ranges(session, pair_id, timeframe, start, until, min_gap))
    log.info("%s: backfilling %s candles from %s, %d missing stretches", symbol, timeframe.code, start,
             len(ranges))

    result = BackfillResult()
    buffer: list[Candle] = []

    def flush() -> None:
        if not buffer:
            return
        with sessions.begin() as session:
            result.candles_written += repo.upsert_candles(session, pair_id, timeframe, buffer)
        result.last_candle = buffer[-1].open_time
        log.info("%s: saved candles up to %s (%d trades so far)", symbol, result.last_candle, result.trades)
        buffer.clear()

    try:
        for range_start, range_end in ranges:
            if progress:
                progress(start, until, range_start, result.trades)
            aggregator = CandleAggregator(timeframe)
            cursor = str(int(range_start.timestamp()))
            try:
                while True:
                    trades, next_cursor = client.trades(symbol, cursor)
                    trades = [t for t in trades if range_start <= t.time < range_end]
                    result.trades += len(trades)
                    buffer.extend(aggregator.add(trades))
                    if progress and trades:
                        progress(start, until, trades[-1].time, result.trades)
                    if len(buffer) >= FLUSH_EVERY:
                        flush()
                    if next_cursor == cursor or not trades:
                        break
                    cursor = next_cursor
            finally:
                # Also keep the bucket still being built – the next run (or OHLC sync) completes it.
                if (last := aggregator.pending()) is not None:
                    buffer.append(last)
    finally:
        flush()
    return result
