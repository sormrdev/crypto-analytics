"""Rebuilds historical candles from raw trades.

Kraken's OHLC endpoint only exposes the last 720 candles, but its Trades endpoint can be paged back to
the very first trade. Aggregating trades locally therefore gives a complete history, including VWAP.
"""

import logging
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
             start: datetime | None = None, until: datetime | None = None) -> BackfillResult:
    """Page through trades from `start` up to `until` (default: now).

    Without `start`, fills the first hole too large for the OHLC endpoint (`ohlc sync` keeps the recent
    window current, so such holes can only appear further back), or else continues after the newest
    stored candle. Safe to interrupt: everything aggregated so far is written before exiting."""
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

    aggregator = CandleAggregator(timeframe)
    result = BackfillResult()
    buffer: list[Candle] = []
    cursor = str(int(start.timestamp()))
    log.info("%s: backfilling %s candles from %s", symbol, timeframe.code, start)

    def flush(extra: Candle | None = None) -> None:
        rows = buffer + ([extra] if extra else [])
        if not rows:
            return
        with sessions.begin() as session:
            result.candles_written += repo.upsert_candles(session, pair_id, timeframe, rows)
        result.last_candle = rows[-1].open_time
        log.info("%s: saved candles up to %s (%d trades so far)", symbol, result.last_candle, result.trades)
        buffer.clear()

    try:
        while True:
            trades, next_cursor = client.trades(symbol, cursor)
            trades = [t for t in trades if start <= t.time < until]
            result.trades += len(trades)
            buffer.extend(aggregator.add(trades))
            if len(buffer) >= FLUSH_EVERY:
                flush()
            if next_cursor == cursor or not trades:
                break
            cursor = next_cursor
    finally:
        # Also persist the bucket still being built – the next run (or OHLC sync) completes it.
        flush(aggregator.pending())
    return result
