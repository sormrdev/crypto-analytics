"""Command-line entry point: `crypto --help`."""

import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer

from crypto_analytics.config import get_settings, postgres_env
from crypto_analytics.db import repository as repo
from crypto_analytics.db.session import session_factory
from crypto_analytics.domain import Timeframe
from crypto_analytics.kraken import KrakenClient
from crypto_analytics.services import backfill as backfill_service
from crypto_analytics.services import sync

app = typer.Typer(help="Kraken spot market data pipeline.", no_args_is_help=True)
pairs_app = typer.Typer(help="Trading pair metadata.", no_args_is_help=True)
ohlc_app = typer.Typer(help="Recent candles from Kraken's OHLC endpoint.", no_args_is_help=True)
app.add_typer(pairs_app, name="pairs")
app.add_typer(ohlc_app, name="ohlc")

TimeframeOpt = Annotated[str, typer.Option("--timeframe", "-t", help="Candle interval, e.g. 15m, 1h, 1d.")]

DBT_DIR = Path(__file__).resolve().parents[2] / "dbt"


def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).replace(tzinfo=UTC) if value else None


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    level = logging.DEBUG if verbose else get_settings().log_level
    logging.basicConfig(level=level, format="%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)


@pairs_app.command("sync")
def pairs_sync() -> None:
    """Add USD pairs listed on Kraken that are not in the database yet."""
    with KrakenClient(get_settings()) as client:
        added = sync.sync_pairs(client, session_factory())
    typer.echo(f"Added {len(added)} pairs" + (f": {', '.join(added)}" if added else ""))


@ohlc_app.command("sync")
def ohlc_sync(
    symbols: Annotated[list[str] | None, typer.Argument(help="Pairs to sync (default: all).")] = None,
    timeframe: TimeframeOpt = "15m",
) -> None:
    """Fetch candles newer than the last stored one (and refresh that one)."""
    with KrakenClient(get_settings()) as client:
        result = sync.sync_ohlc(client, session_factory(), Timeframe.from_code(timeframe), symbols)
    typer.echo(f"{result.pairs} pairs synced, {result.candles_written} candles written.")
    if result.truncated:
        typer.echo(f"Gaps too large for the OHLC endpoint - run backfill for: {', '.join(result.truncated)}")
    if result.failed:
        typer.echo(f"Failed: {', '.join(result.failed)}", err=True)
        raise typer.Exit(1)


@app.command()
def backfill(
    symbol: str,
    since: Annotated[str | None, typer.Option(help="Start date YYYY-MM-DD (default: resume).")] = None,
    until: Annotated[str | None, typer.Option(help="End date YYYY-MM-DD (default: now).")] = None,
    timeframe: TimeframeOpt = "15m",
) -> None:
    """Rebuild candles from raw trades – complete history, including VWAP. Ctrl+C is safe."""
    with KrakenClient(get_settings()) as client:
        result = backfill_service.backfill(client, session_factory(), symbol, Timeframe.from_code(timeframe),
                                           _date(since), _date(until))
    typer.echo(f"{symbol}: {result.trades} trades -> {result.candles_written} candles written "
               f"(up to {result.last_candle}).")


@app.command()
def gaps(
    symbol: Annotated[str | None, typer.Argument(help="Pair to inspect (default: summary of all pairs).")] = None,
    timeframe: TimeframeOpt = "15m",
    min_missing: Annotated[int, typer.Option(help="Report only holes of at least N candles.")] = 4,
    since: Annotated[str | None, typer.Option(help="Only look at candles from this date (YYYY-MM-DD).")] = None,
    top: Annotated[int, typer.Option(help="Pairs to show in the summary.")] = 20,
) -> None:
    """Data-quality check: list holes in the stored candle history (times in UTC)."""
    tf = Timeframe.from_code(timeframe)
    with session_factory()() as session:
        if symbol is None:
            summary = repo.gap_summary(session, tf, min_missing, _date(since))
        else:
            pair_id = repo.pair_ids(session, [symbol]).get(symbol)
            if pair_id is None:
                raise typer.BadParameter(f"Unknown symbol {symbol}")
            found = repo.find_gaps(session, pair_id, tf, min_missing, _date(since))

    if symbol is None:
        typer.echo(f"{'pair':<14}{'gaps':>6}{'missing':>10}{'largest':>10}  last candle (UTC)")
        for row in summary[:top]:
            typer.echo(f"{row.symbol:<14}{row.gaps:>6}{row.missing_candles:>10}{row.largest_gap:>10}  "
                       f"{row.last_candle.astimezone(UTC):%Y-%m-%d %H:%M}")
        with_gaps = sum(1 for row in summary if row.gaps)
        typer.echo(f"{with_gaps} of {len(summary)} pairs have gaps of >= {min_missing} {tf.code} candles"
                   + (f" (showing {top})." if len(summary) > top else "."))
        return

    for gap in found:
        after, before = gap.after.astimezone(UTC), gap.before.astimezone(UTC)
        typer.echo(f"{after:%Y-%m-%d %H:%M} -> {before:%Y-%m-%d %H:%M}  "
                   f"({int(gap.missing.total_seconds() // tf.seconds) - 1} candles missing)")
    typer.echo(f"{len(found)} gaps of >= {min_missing} candles in {symbol} {tf.code}.")
    if found:
        biggest = max(found, key=lambda g: g.missing)
        typer.echo(f"Fill the largest with: crypto backfill {symbol} "
                   f"--since {biggest.after.astimezone(UTC):%Y-%m-%dT%H:%M} "
                   f"--until {biggest.before.astimezone(UTC):%Y-%m-%dT%H:%M}")


@app.command(context_settings={"allow_extra_args": True, "ignore_unknown_options": True})
def dbt(ctx: typer.Context) -> None:
    """Run dbt against CRYPTO_DATABASE_URL, e.g. `crypto dbt build` or `crypto dbt source freshness`."""
    env = os.environ | postgres_env(get_settings().database_url)
    # PYTHONUTF8: dbt prints non-ASCII symbols, which a Windows console code page cannot encode.
    env |= {"DBT_PROJECT_DIR": str(DBT_DIR), "DBT_PROFILES_DIR": str(DBT_DIR), "PYTHONUTF8": "1"}
    raise typer.Exit(subprocess.run(["dbt", *ctx.args], env=env).returncode)


if __name__ == "__main__":
    app()
