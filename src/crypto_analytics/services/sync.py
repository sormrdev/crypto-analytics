"""Incremental syncs from Kraken's REST endpoints (pairs and recent OHLC)."""

import logging
from collections.abc import Collection, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

from sqlalchemy.orm import Session, sessionmaker

from crypto_analytics.db import repository as repo
from crypto_analytics.domain import PairActivity, Timeframe
from crypto_analytics.kraken import KrakenAPIError, KrakenClient

log = logging.getLogger(__name__)

# Kraken's OHLC endpoint never returns more than this many candles, whatever `since` is.
OHLC_WINDOW = 720


def crypto_by_24h_turnover(activity: Iterable[PairActivity], excluded_bases: Collection[str]) -> list[PairActivity]:
    """Pairs sorted by 24h USD turnover, without fiat, gold (`excluded_bases`) and USD stablecoins."""
    return sorted((a for a in activity
                   if a.symbol.removesuffix("USD") not in excluded_bases and not a.is_usd_pegged),
                  key=lambda a: a.turnover_usd, reverse=True)


def rank_pairs(client: KrakenClient, top: int, excluded_bases: Collection[str],
               days: int = 30) -> list[tuple[str, Decimal]]:
    """The `top` most traded crypto pairs by USD turnover over the last `days` days.

    One day is too noisy to choose a fixed list from, but measuring every USD pair over a longer window
    takes one OHLC call each (600+). So the 3 x `top` busiest pairs of the last 24 hours (one Ticker call)
    are shortlisted and only those are measured from daily candles."""
    shortlist = crypto_by_24h_turnover(client.usd_pair_activity(), excluded_bases)[:3 * top]
    since = datetime.now(UTC) - timedelta(days=days)
    turnover: dict[str, Decimal] = {}
    for pair in shortlist:
        candles = client.ohlc(pair.symbol, Timeframe.D1, since)
        turnover[pair.symbol] = sum((c.vwap * c.volume for c in candles if c.vwap is not None), Decimal(0))
        log.info("%s: %s USD in %d days", pair.symbol, f"{turnover[pair.symbol]:,.0f}", days)
    return sorted(turnover.items(), key=lambda item: item[1], reverse=True)[:top]


def write_whitelist(path: Path, ranked: Sequence[tuple[str, Decimal]], days: int) -> None:
    lines = [f"# Tracked pairs: the {len(ranked)} most traded crypto pairs on Kraken by USD turnover over the",
             f"# {days} days to {datetime.now(UTC):%Y-%m-%d}, written by `crypto pairs rank`. Fixed on purpose: a",
             "# list re-ranked on every run would drop pairs near the cut-off and leave holes in their data.",
             "# Apply changes with `crypto pairs sync`.",
             *(f"{symbol:<14}# {value:,.0f} USD" for symbol, value in ranked)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def read_whitelist(path: Path) -> list[str]:
    return [symbol for line in path.read_text(encoding="utf-8").splitlines()
            if (symbol := line.split("#", 1)[0].strip())]


def apply_whitelist(sessions: sessionmaker[Session], symbols: Sequence[str]) -> tuple[list[str], list[str]]:
    """Track exactly `symbols`. Returns the pairs that joined (they need a backfill for older history)
    and the pairs that left (`crypto pairs prune` deletes them)."""
    with sessions.begin() as session:
        current = repo.tracked_symbols(session)
        repo.set_tracked_pairs(session, symbols)
    return sorted(set(symbols) - current), sorted(current - set(symbols))


@dataclass
class OhlcSyncResult:
    pairs: int = 0
    candles_written: int = 0
    failed: list[str] = field(default_factory=list)
    truncated: list[str] = field(default_factory=list)  # gap exceeded the OHLC window -> run a backfill


def sync_ohlc(client: KrakenClient, sessions: sessionmaker[Session], timeframe: Timeframe,
              symbols: Sequence[str] | None = None) -> OhlcSyncResult:
    result = OhlcSyncResult()
    with sessions() as session:
        targets = repo.pair_ids(session, symbols)
    missing = set(symbols or []) - targets.keys()
    if missing:
        log.warning("Unknown symbols (run `crypto pairs sync` first): %s", ", ".join(sorted(missing)))

    for symbol, pair_id in targets.items():
        try:
            with sessions.begin() as session:
                since = repo.latest_candle_time(session, pair_id, timeframe)
                # Re-request the last stored candle: it may have been saved while still forming.
                candles = client.ohlc(symbol, timeframe, since - timeframe.delta if since else None)
                written = repo.upsert_candles(session, pair_id, timeframe, candles)
        except KrakenAPIError as exc:
            log.error("%s: %s", symbol, exc)
            result.failed.append(symbol)
            continue
        if since and candles and candles[0].open_time > since:
            result.truncated.append(symbol)
            log.warning("%s: data gap since %s is larger than Kraken's %d-candle window; "
                        "fill it with `crypto backfill`", symbol, since, OHLC_WINDOW)
        result.pairs += 1
        result.candles_written += written
        log.info("%s: %d candles received, %d written", symbol, len(candles), written)
    return result
