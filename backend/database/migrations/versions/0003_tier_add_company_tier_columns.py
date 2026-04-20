"""add_company_tier_columns

Phase 6: rank jobs by company quality, not just JD fit.

Adds three nullable columns to `jobs`:
    - company_tier      (top_tier / unicorn / growth_startup / early_startup / other)
    - funding_stage     (seed, series_a..f, pre_ipo, public, bootstrapped, unknown)
    - headcount_band    (<50, 50-200, 200-1000, 1000-5000, 5000+)

Also indexes company_tier since it's a common dashboard filter.

Revision ID: 0003_tier
Revises: 0002_indexes
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0003_tier"
down_revision: Union[str, None] = "0002_indexes"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(sa.Column("company_tier", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("funding_stage", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("headcount_band", sa.String(length=30), nullable=True))

    op.create_index("ix_jobs_company_tier", "jobs", ["company_tier"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_company_tier", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("headcount_band")
        batch.drop_column("funding_stage")
        batch.drop_column("company_tier")
