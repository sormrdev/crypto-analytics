"""Command-line entry point: `crypto --help`."""

import logging
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import BarColumn, Progress, TaskProgressColumn, TextColumn, TimeElapsedColumn, TimeRemainingColumn

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

ROOT = Path(__file__).resolve().parents[2]
DBT_DIR = ROOT / "dbt"
# Logs and progress bars share one console, so log lines print above a running bar instead of through it.
console = Console(stderr=True)
WHITELIST = ROOT / "tracked_pairs.txt"


def _date(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value).replace(tzinfo=UTC) if value else None


@app.callback()
def main(verbose: Annotated[bool, typer.Option("--verbose", "-v")] = False) -> None:
    level = logging.DEBUG if verbose else get_settings().log_level
    logging.basicConfig(level=level, format="%(name)s: %(message)s", datefmt="%H:%M:%S",
                        handlers=[RichHandler(console=console, show_path=False)])
    logging.getLogger("httpx").setLevel(logging.WARNING)


@pairs_app.command("rank")
def pairs_rank(
    top: Annotated[int | None, typer.Option(help="Pairs to keep (default: CRYPTO_TOP_PAIRS, 50).")] = None,
    days: Annotated[int, typer.Option(help="Turnover window in days.")] = 30,
) -> None:
    """Rank USD crypto pairs by turnover (no fiat, gold or stablecoins) and write tracked_pairs.txt."""
    settings = get_settings()
    with KrakenClient(settings) as client:
        ranked = sync.rank_pairs(client, top or settings.top_pairs, settings.excluded_bases, days)
    for rank, (symbol, turnover) in enumerate(ranked, 1):
        typer.echo(f"{rank:>3}  {symbol:<14}{turnover:>18,.0f} USD")
    sync.write_whitelist(WHITELIST, ranked, days)
    typer.echo(f"Wrote {len(ranked)} pairs to {WHITELIST.name}; apply them with `crypto pairs sync`.")


@pairs_app.command("sync")
def pairs_sync() -> None:
    """Track exactly the pairs in tracked_pairs.txt (adds new ones to the database)."""
    joined, dropped = sync.apply_whitelist(session_factory(), sync.read_whitelist(WHITELIST))
    typer.echo(f"Tracking the {len(sync.read_whitelist(WHITELIST))} pairs in {WHITELIST.name}.")
    if joined:
        typer.echo(f"Newly tracked: {', '.join(joined)}. `ohlc sync` gets their last 7.5 days; "
                   "older history needs `crypto backfill`.")
    if dropped:
        more = f" and {len(dropped) - 10} more" if len(dropped) > 10 else ""
        typer.echo(f"No longer tracked: {', '.join(dropped[:10])}{more} (delete them with `crypto pairs prune`).")


@pairs_app.command("prune")
def pairs_prune(yes: Annotated[bool, typer.Option("--yes", help="Delete; without it, only list.")] = False) -> None:
    """Delete pairs that are not tracked, with all their candles."""
    sessions = session_factory()
    with sessions() as session:
        untracked = repo.untracked_pairs(session)
    candles = sum(count for _, count in untracked)
    typer.echo(f"{len(untracked)} untracked pairs with {candles:,} candles"
               + (f", e.g. {', '.join(symbol for symbol, _ in untracked[:10])}." if untracked else "."))
    if not untracked or not yes:
        if untracked:
            typer.echo("Run again with --yes to delete them.")
        return
    with sessions.begin() as session:
        deleted = repo.delete_untracked_pairs(session)
    typer.echo(f"Deleted {len(untracked)} pairs and {deleted:,} candles.")


@ohlc_app.command("sync")
def ohlc_sync(
    symbols: Annotated[list[str] | None, typer.Argument(help="Pairs to sync (default: tracked pairs).")] = None,
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
    symbols: Annotated[list[str] | None, typer.Argument(help="Pairs to backfill (default: tracked pairs).")] = None,
    since: Annotated[str | None, typer.Option(help="Start date YYYY-MM-DD (default: first large gap).")] = None,
    until: Annotated[str | None, typer.Option(help="End date YYYY-MM-DD (default: now).")] = None,
    overwrite: Annotated[bool, typer.Option(help="Re-fetch candles that are already stored.")] = False,
    min_gap: Annotated[int, typer.Option(help="Skip holes shorter than N candles.")] = 4,
    timeframe: TimeframeOpt = "15m",
) -> None:
    """Rebuild candles from raw trades – complete history, including VWAP. Ctrl+C is safe.

    Only missing candles are fetched unless --overwrite. Without --since, each pair's first gap too large
    for `ohlc sync` is filled (run again for the next)."""
    sessions = session_factory()
    if not symbols:
        with sessions() as session:
            symbols = list(repo.pair_ids(session))
    columns = (TextColumn("{task.description:<12}"), BarColumn(), TaskProgressColumn(),
               TextColumn("{task.fields[reached]}  {task.fields[trades]:>10,} trades"),
               TimeElapsedColumn(), TextColumn("left"), TimeRemainingColumn())
    with KrakenClient(get_settings()) as client, Progress(*columns, console=console) as bar:
        overall = bar.add_task("all pairs", total=len(symbols), reached="", trades=0) if len(symbols) > 1 else None
        for symbol in symbols:
            task = bar.add_task(symbol, total=None, reached="", trades=0)

            def show(start: datetime, end: datetime, reached: datetime, trades: int, task=task) -> None:
                # Progress is measured in time covered: trades arrive oldest first.
                bar.update(task, total=(end - start).total_seconds(), completed=(reached - start).total_seconds(),
                           reached=f"{reached.astimezone(UTC):%Y-%m-%d %H:%M}", trades=trades)

            result = backfill_service.backfill(client, sessions, symbol, Timeframe.from_code(timeframe),
                                               _date(since), _date(until), overwrite, min_gap, progress=show)
            bar.remove_task(task)
            bar.console.print(f"{symbol}: {result.trades:,} trades -> {result.candles_written:,} candles written "
                              f"(up to {result.last_candle}).")
            if overall is not None:
                bar.advance(overall)


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
