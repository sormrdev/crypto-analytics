"""Baseline schema: pair, timeframe, ohlc_candle.

Matches the database that existed before migrations were introduced, so on that database this revision
is a no-op; on an empty database it creates everything.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-10-17
"""
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import TIMESTAMP

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None

TIMEFRAMES = [(1, "1m", 60), (2, "5m", 300), (3, "15m", 900), (4, "1h", 3600),
              (5, "4h", 14400), (6, "1d", 86400), (9, "30m", 1800)]


def upgrade() -> None:
    if sa.inspect(op.get_bind()).has_table("ohlc_candle"):
        return

    op.create_table(
        "pair",
        sa.Column("pair_id", sa.Integer, primary_key=True),
        sa.Column("symbol", sa.String(30), nullable=False, unique=True),
    )
    timeframe = op.create_table(
        "timeframe",
        sa.Column("timeframe_id", sa.SmallInteger, primary_key=True),
        sa.Column("code", sa.String(10), nullable=False, unique=True),
        sa.Column("duration_seconds", sa.Integer, nullable=False, unique=True),
    )
    op.bulk_insert(timeframe, [{"timeframe_id": i, "code": c, "duration_seconds": s} for i, c, s in TIMEFRAMES])
    op.create_table(
        "ohlc_candle",
        sa.Column("pair_id", sa.Integer, sa.ForeignKey("pair.pair_id"), nullable=False),
        sa.Column("timeframe_id", sa.SmallInteger, sa.ForeignKey("timeframe.timeframe_id"), nullable=False),
        sa.Column("candle_time", TIMESTAMP(timezone=True), nullable=False),
        sa.Column("open_price", sa.Numeric, nullable=False),
        sa.Column("high_price", sa.Numeric, nullable=False),
        sa.Column("low_price", sa.Numeric, nullable=False),
        sa.Column("close_price", sa.Numeric, nullable=False),
        sa.Column("volume", sa.Numeric, nullable=False),
        sa.Column("vwap", sa.Numeric, nullable=True),
        sa.PrimaryKeyConstraint("pair_id", "timeframe_id", "candle_time"),
        sa.CheckConstraint("high_price >= open_price AND high_price >= close_price AND high_price >= low_price",
                           name="chk_high_price"),
        sa.CheckConstraint("low_price <= open_price AND low_price <= close_price AND low_price <= high_price",
                           name="chk_low_price"),
        sa.CheckConstraint("volume >= 0", name="chk_volume_positive"),
    )
    op.execute("CREATE INDEX idx_ohlc_pair_time ON ohlc_candle (pair_id, candle_time DESC)")
    op.execute("CREATE INDEX idx_ohlc_timeframe_time ON ohlc_candle (timeframe_id, candle_time DESC)")


def downgrade() -> None:
    # Deliberately irreversible: dropping these tables would delete years of collected market data.
    raise NotImplementedError("Refusing to drop the baseline tables – restore from a pg_dump instead.")
