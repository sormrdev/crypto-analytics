"""Relabel 15-minute candles that were stored with the 1-minute timeframe id.

The original collectors used `timeframe_id = 1` for 15-minute data, but id 1 is '1m' in the timeframe
table (15m is id 3). Every stored candle is 900 s apart, so the fix is a plain relabel.

Revision ID: 0002_relabel_15m_candles
Revises: 0001_baseline
Create Date: 2026-10-17
"""
from alembic import op
from sqlalchemy import text

revision = "0002_relabel_15m_candles"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    # Sanity check before touching millions of rows: the "1m" candles must really be 15 minutes apart.
    off_grid = bind.scalar(text(
        "SELECT count(*) FROM ohlc_candle WHERE timeframe_id = 1 "
        "AND extract(epoch FROM candle_time)::bigint % 900 <> 0"))
    if off_grid:
        raise RuntimeError(f"{off_grid} candles labelled 1m are not on a 15-minute grid; aborting relabel")
    op.execute("UPDATE ohlc_candle SET timeframe_id = 3 WHERE timeframe_id = 1")


def downgrade() -> None:
    op.execute("UPDATE ohlc_candle SET timeframe_id = 1 WHERE timeframe_id = 3")
