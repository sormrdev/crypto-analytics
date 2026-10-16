"""Remove placeholder candles stamped 1970-01-01 and forbid implausible candle times.

The original collectors stored one candle with a zero Unix timestamp for 59 freshly listed pairs.
Kraken spot launched in 2013, so anything earlier is invalid by definition.

Revision ID: 0003_remove_epoch_candles
Revises: 0002_relabel_15m_candles
Create Date: 2026-10-17
"""
from alembic import op

revision = "0003_remove_epoch_candles"
down_revision = "0002_relabel_15m_candles"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DELETE FROM ohlc_candle WHERE candle_time < '2013-01-01'")
    op.create_check_constraint("chk_candle_time_plausible", "ohlc_candle", "candle_time >= '2013-01-01'")


def downgrade() -> None:
    # The deleted rows were junk; only the constraint is reverted.
    op.drop_constraint("chk_candle_time_plausible", "ohlc_candle", type_="check")
