"""Hand the reporting layer over to dbt.

reporting.candles_15m is now built by the dbt project (`crypto dbt build`), together with the other
reporting tables. Alembic keeps owning the raw tables only; run dbt after migrating.

Revision ID: 0005_reporting_layer_to_dbt
Revises: 0004_reporting_view
Create Date: 2026-10-17
"""
from alembic import op

revision = "0005_reporting_layer_to_dbt"
down_revision = "0004_reporting_view"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("DROP VIEW IF EXISTS reporting.candles_15m")


def downgrade() -> None:
    # Nothing to restore here: dbt recreates the view, and downgrading past 0004 drops the schema.
    pass
