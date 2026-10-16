"""Incremental syncs from Kraken's REST endpoints (pairs and recent OHLC)."""

import logging
from collections.abc import Sequence
from dataclasses import dataclass

from sqlalchemy.orm import Session, sessionmaker

from crypto_analytics.db import repository as repo
from crypto_analytics.domain import Timeframe
from crypto_analytics.kraken import KrakenAPIError, KrakenClient

log = logging.getLogger(__name__)

# Kraken's OHLC endpoint never returns more than this many candles, whatever `since` is.
OHLC_WINDOW = 720


def sync_pairs(client: KrakenClient, sessions: sessionmaker[Session]) -> list[str]:
    symbols = client.usd_pairs()
    with sessions.begin() as session:
        added = repo.add_missing_pairs(session, symbols)
    log.info("Kraken lists %d USD pairs, %d new", len(symbols), len(added))
    return added


@dataclass
class OhlcSyncResult:
    pairs: int = 0
    candles_written: int = 0
    failed: list[str] | None = None
    truncated: list[str] | None = None  # pairs whose gap exceeded the OHLC window -> run a backfill


def sync_ohlc(client: KrakenClient, sessions: sessionmaker[Session], timeframe: Timeframe,
              symbols: Sequence[str] | None = None) -> OhlcSyncResult:
    result = OhlcSyncResult(failed=[], truncated=[])
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
