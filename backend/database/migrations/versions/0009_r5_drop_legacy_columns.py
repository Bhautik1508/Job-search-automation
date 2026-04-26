"""r5_drop_legacy_columns

Phase R5: drop legacy columns + indexes that the simplified pipeline
no longer reads.

- `applied` (bool) — replaced by the R2 `status` enum.
- `company_tier`, `funding_stage`, `headcount_band` — tier classifier
  is being deleted; nothing populates these columns anymore.
- `ix_jobs_applied_relevancy`, `ix_jobs_company_tier` — indexes on
  the dropped columns.

Revision ID: 0009_r5_cleanup
Revises: 0008_connections
Create Date: 2026-04-26
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0009_r5_cleanup"
down_revision: Union[str, None] = "0008_connections"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index("ix_jobs_applied_relevancy", table_name="jobs")
    op.drop_index("ix_jobs_company_tier", table_name="jobs")

    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("applied")
        batch.drop_column("company_tier")
        batch.drop_column("funding_stage")
        batch.drop_column("headcount_band")


def downgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column(
                "applied",
                sa.Boolean(),
                nullable=False,
                server_default=sa.text("0"),
            )
        )
        batch.add_column(sa.Column("company_tier", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("funding_stage", sa.String(length=30), nullable=True))
        batch.add_column(sa.Column("headcount_band", sa.String(length=30), nullable=True))

    # Re-derive applied from the surviving status column so the bool stays
    # consistent with the funnel.
    op.execute(
        "UPDATE jobs SET applied = 1 "
        "WHERE status IN ('applied', 'interviewing', 'offer')"
    )

    op.create_index(
        "ix_jobs_applied_relevancy", "jobs", ["applied", "relevancy_score"]
    )
    op.create_index("ix_jobs_company_tier", "jobs", ["company_tier"])
