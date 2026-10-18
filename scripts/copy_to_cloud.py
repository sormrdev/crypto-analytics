"""One-off: copy the most traded pairs from the local database into a new (cloud) database.

A free hosted PostgreSQL is usually too small for every pair, so only the top N pairs by turnover
are copied and marked as tracked (the scheduled `pairs sync` then applies tracked_pairs.txt).

    CRYPTO_DATABASE_URL=<cloud url> uv run alembic upgrade head          # empty schema in the cloud first
    TARGET_DATABASE_URL=<cloud url> uv run python scripts/copy_to_cloud.py --top 50

The source is your normal CRYPTO_DATABASE_URL (.env); `crypto dbt build` must have run there.
"""

import argparse
import os

import psycopg
from sqlalchemy.engine import make_url

from crypto_analytics.config import get_settings

CANDLE_COLUMNS = ("pair_id, timeframe_id, candle_time, open_price, high_price, low_price, close_price, "
                  "volume, vwap")


def libpq(url: str) -> str:
    return make_url(url).set(drivername="postgresql").render_as_string(hide_password=False)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--top", type=int, default=50, help="number of pairs to copy, by turnover")
    args = parser.parse_args()
    target_url = os.environ.get("TARGET_DATABASE_URL")
    if not target_url:
        raise SystemExit("Set TARGET_DATABASE_URL to the cloud database first.")

    with psycopg.connect(libpq(get_settings().database_url)) as src, psycopg.connect(libpq(target_url)) as dst:
        if dst.execute("SELECT EXISTS (SELECT 1 FROM ohlc_candle)").fetchone()[0]:
            raise SystemExit("The target already has candles - copy only into a freshly migrated database.")
        top = src.execute("SELECT pair_id, symbol FROM reporting.pairs ORDER BY turnover_usd DESC LIMIT %s",
                          (args.top,)).fetchall()
        ids = [pair_id for pair_id, _ in top]

        # All pairs keep their ids (small table), then the serial continues after the highest one.
        pairs = "COPY (SELECT pair_id, symbol, pair_id = ANY(%s) FROM pair) TO STDOUT"
        with src.cursor().copy(pairs, (ids,)) as out, \
                dst.cursor().copy("COPY pair (pair_id, symbol, tracked) FROM STDIN") as inp:
            for chunk in out:
                inp.write(chunk)
        dst.execute("SELECT setval(pg_get_serial_sequence('pair', 'pair_id'), (SELECT max(pair_id) FROM pair))")

        query = f"COPY (SELECT {CANDLE_COLUMNS} FROM ohlc_candle WHERE pair_id = ANY(%s)) TO STDOUT"
        with src.cursor().copy(query, (ids,)) as out, \
                dst.cursor().copy(f"COPY ohlc_candle ({CANDLE_COLUMNS}) FROM STDIN") as inp:
            for chunk in out:
                inp.write(chunk)
        copied = dst.execute("SELECT count(*) FROM ohlc_candle").fetchone()[0]

    print(f"Copied {copied:,} candles for {len(top)} pairs: {' '.join(symbol for _, symbol in top)}")


if __name__ == "__main__":
    main()
