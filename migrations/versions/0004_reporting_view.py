"""Reporting view consumed by Power BI.

Keeps report logic in the database, versioned with the rest of the schema:
- 15-minute candles of the last 365 days,
- only pairs with at least 1M USD turnover in that window,
- times as UTC `timestamp without time zone`, so Power BI does not shift them to the machine's local zone.

Revision ID: 0004_reporting_view
Revises: 0003_remove_epoch_candles
Create Date: 2026-10-17
"""
from alembic import op

revision = "0004_reporting_view"
down_revision = "0003_remove_epoch_candles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE SCHEMA IF NOT EXISTS reporting")
    op.execute("""
        CREATE OR REPLACE VIEW reporting.candles_15m AS
        WITH recent AS (
            SELECT c.* FROM ohlc_candle c
            JOIN timeframe t USING (timeframe_id)
            WHERE t.code = '15m' AND c.candle_time >= now() - interval '365 days'
        ),
        liquid AS (
            SELECT pair_id FROM recent
            GROUP BY pair_id
            HAVING sum(volume * close_price) >= 1000000
        )
        SELECT r.pair_id,
               r.timeframe_id,
               p.symbol,
               r.candle_time AT TIME ZONE 'UTC' AS candle_time_utc,
               r.open_price, r.high_price, r.low_price, r.close_price, r.volume, r.vwap
        FROM recent r
        JOIN liquid USING (pair_id)
        JOIN pair p USING (pair_id)
    """)


def downgrade() -> None:
    op.execute("DROP VIEW IF EXISTS reporting.candles_15m")
    op.execute("DROP SCHEMA IF EXISTS reporting")
