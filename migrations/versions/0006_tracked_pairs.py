"""Whitelist of tracked pairs.

`crypto pairs sync` marks the pairs listed in tracked_pairs.txt as tracked; `ohlc sync` and the report use
only those, and `crypto pairs prune` deletes the rest. Existing pairs start as tracked so nothing changes
until the whitelist is first applied.

Revision ID: 0006_tracked_pairs
Revises: 0005_reporting_layer_to_dbt
Create Date: 2026-10-18
"""
import sqlalchemy as sa
from alembic import op

revision = "0006_tracked_pairs"
down_revision = "0005_reporting_layer_to_dbt"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("pair", sa.Column("tracked", sa.Boolean(), nullable=False, server_default=sa.true()))
    op.alter_column("pair", "tracked", server_default=sa.false())


def downgrade() -> None:
    # CASCADE: the dbt views reading the column go too; `crypto dbt build` recreates them.
    op.execute("ALTER TABLE pair DROP COLUMN tracked CASCADE")
