"""All SQL used by the pipeline. Services talk to the database only through this module."""

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from itertools import batched

from sqlalchemy import func, select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from crypto_analytics.domain import Candle, Timeframe

from .models import OhlcCandle, Pair

# PostgreSQL allows 65 535 bind parameters per statement; a candle row uses 9.
UPSERT_BATCH = 5000


def add_missing_pairs(session: Session, symbols: Iterable[str]) -> list[str]:
    symbols = list(symbols)
    if not symbols:
        return []
    stmt = (insert(Pair).values([{"symbol": s} for s in symbols])
            .on_conflict_do_nothing(index_elements=["symbol"]).returning(Pair.symbol))
    return list(session.scalars(stmt))


def pair_ids(session: Session, symbols: Sequence[str] | None = None) -> dict[str, int]:
    stmt = select(Pair.symbol, Pair.pair_id).order_by(Pair.symbol)
    if symbols:
        stmt = stmt.where(Pair.symbol.in_(symbols))
    return {symbol: pair_id for symbol, pair_id in session.execute(stmt)}


def latest_candle_time(session: Session, pair_id: int, timeframe: Timeframe) -> datetime | None:
    return session.scalar(
        select(func.max(OhlcCandle.candle_time))
        .where(OhlcCandle.pair_id == pair_id, OhlcCandle.timeframe_id == timeframe.db_id)
    )


def upsert_candles(session: Session, pair_id: int, timeframe: Timeframe, candles: Iterable[Candle]) -> int:
    """Insert new candles and refresh changed ones.

    Updating (rather than skipping) conflicts matters: the newest candle from Kraken is still forming,
    so a later run must be able to overwrite it with the final values.
    """
    written = 0
    for chunk in batched(candles, UPSERT_BATCH):
        stmt = insert(OhlcCandle).values([
            {
                "pair_id": pair_id, "timeframe_id": timeframe.db_id, "candle_time": c.open_time,
                "open_price": c.open, "high_price": c.high, "low_price": c.low, "close_price": c.close,
                "volume": c.volume, "vwap": c.vwap,
            }
            for c in chunk
        ])
        changed = {col: stmt.excluded[col] for col in
                   ("open_price", "high_price", "low_price", "close_price", "volume", "vwap")}
        stmt = stmt.on_conflict_do_update(
            index_elements=["pair_id", "timeframe_id", "candle_time"],
            set_=changed,
            # Skip no-op rewrites so re-running a sync is cheap.
            where=(func.row(*[getattr(OhlcCandle, c) for c in changed])
                   .is_distinct_from(func.row(*changed.values()))),
        ).returning(OhlcCandle.candle_time)
        # RETURNING lists only rows actually inserted or updated (rowcount is unreliable for this form).
        written += len(session.scalars(stmt).all())
    return written


@dataclass(frozen=True)
class Gap:
    after: datetime
    before: datetime

    @property
    def missing(self) -> timedelta:
        return self.before - self.after


def find_gaps(session: Session, pair_id: int, timeframe: Timeframe, min_missing: int = 1,
              since: datetime | None = None) -> list[Gap]:
    """Holes of at least `min_missing` consecutive candles. Illiquid pairs legitimately have
    empty intervals (no trades), so treat small gaps as information rather than errors."""
    rows = session.execute(text("""
        SELECT prev_time, candle_time FROM (
            SELECT candle_time, lag(candle_time) OVER (ORDER BY candle_time) AS prev_time
            FROM ohlc_candle
            WHERE pair_id = :pair_id AND timeframe_id = :tf
              AND (CAST(:since AS timestamptz) IS NULL OR candle_time >= :since)
        ) t
        WHERE candle_time - prev_time > make_interval(secs => :step * (:min_missing + 1) - 1)
        ORDER BY candle_time
    """), {"pair_id": pair_id, "tf": timeframe.db_id, "step": timeframe.seconds, "min_missing": min_missing,
           "since": since})
    return [Gap(after, before) for after, before in rows]


@dataclass(frozen=True)
class GapSummary:
    symbol: str
    gaps: int
    missing_candles: int
    largest_gap: int  # candles missing in the biggest hole
    last_candle: datetime  # a stale pair has no hole yet, but an old last candle


def gap_summary(session: Session, timeframe: Timeframe, min_missing: int = 1,
                since: datetime | None = None) -> list[GapSummary]:
    """Every pair with candles, most missing data (holes of at least `min_missing` candles) first."""
    rows = session.execute(text("""
        WITH c AS (
            SELECT pair_id, candle_time,
                   lag(candle_time) OVER (PARTITION BY pair_id ORDER BY candle_time) AS prev_time
            FROM ohlc_candle
            WHERE timeframe_id = :tf AND (CAST(:since AS timestamptz) IS NULL OR candle_time >= :since)
        ),
        holes AS (
            SELECT pair_id, extract(epoch FROM candle_time - prev_time)::bigint / :step - 1 AS missing
            FROM c
            WHERE candle_time - prev_time > make_interval(secs => :step * (:min_missing + 1) - 1)
        ),
        latest AS (
            SELECT pair_id, max(candle_time) AS last_candle FROM c GROUP BY pair_id
        )
        SELECT p.symbol, count(h.missing), coalesce(sum(h.missing), 0), coalesce(max(h.missing), 0),
               l.last_candle
        FROM latest l
        JOIN pair p USING (pair_id)
        LEFT JOIN holes h USING (pair_id)
        GROUP BY p.symbol, l.last_candle
        ORDER BY 3 DESC, p.symbol
    """), {"tf": timeframe.db_id, "step": timeframe.seconds, "min_missing": min_missing, "since": since})
    return [GapSummary(symbol, gaps, int(missing), int(largest), last)
            for symbol, gaps, missing, largest, last in rows]
