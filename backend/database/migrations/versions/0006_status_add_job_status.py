"""add_job_status

Phase R2: real application tracking.

Replaces the old `applied: bool` with a richer `status` enum:
    new | saved | applied | interviewing | offer | rejected | hidden

`hidden` is the soft-delete state — UI default filter excludes it
along with `rejected` so the table stays focused on live opportunities.

`applied` (the bool) stays in place as a shadow column for one release
so any external scripts/SQL that still read it don't break. R5 drops it.

Revision ID: 0006_status
Revises: 0005_outreach
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0006_status"
down_revision: Union[str, None] = "0005_outreach"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("jobs") as batch:
        batch.add_column(
            sa.Column(
                "status",
                sa.String(length=20),
                nullable=False,
                server_default="new",
            )
        )

    # Backfill: existing applied=True rows become status=applied.
    # Everything else stays at the server_default 'new'.
    op.execute("UPDATE jobs SET status = 'applied' WHERE applied = 1")
    op.execute("UPDATE jobs SET status = 'applied' WHERE applied = TRUE")

    op.create_index("ix_jobs_status", "jobs", ["status"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_jobs_status", table_name="jobs")
    with op.batch_alter_table("jobs") as batch:
        batch.drop_column("status")
