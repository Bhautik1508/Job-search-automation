"""add_connections_table_and_outreach_connection_link

Phase R4: warm referral layer.

- New `connections` table holds people the user already knows (imported
  from Happenstance/LinkedIn CSV). Independent of `contacts`, which stores
  HMs/recruiters we target.
- `outreach_drafts.connection_id` (nullable FK) tracks which warm
  connection a referral_ask draft is addressed to.
- Replace the (job, contact, channel) unique constraint with one that
  includes connection_id so two referral asks with different warm peers
  but the same intro-target HM can coexist.

Revision ID: 0008_connections
Revises: 0007_case_study
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0008_connections"
down_revision: Union[str, None] = "0007_case_study"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "connections",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("company", sa.String(length=300), nullable=False),
        sa.Column("company_normalized", sa.String(length=300), nullable=False),
        sa.Column("current_title", sa.String(length=500), nullable=True),
        sa.Column("linkedin_url", sa.String(length=1000), nullable=True),
        sa.Column("source", sa.String(length=30), nullable=False, server_default="csv"),
        sa.Column("last_synced_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
    )
    op.create_index("ix_connections_company_normalized", "connections", ["company_normalized"])
    op.create_index("ix_connections_linkedin_url", "connections", ["linkedin_url"])

    with op.batch_alter_table("outreach_drafts") as batch:
        batch.add_column(sa.Column("connection_id", sa.Integer(), nullable=True))
        batch.drop_constraint(
            "uq_outreach_drafts_job_contact_channel",
            type_="unique",
        )
        batch.create_unique_constraint(
            "uq_outreach_drafts_job_contact_channel_connection",
            ["job_id", "contact_id", "channel", "connection_id"],
        )
        batch.create_foreign_key(
            "fk_outreach_drafts_connection_id",
            "connections",
            ["connection_id"],
            ["id"],
            ondelete="SET NULL",
        )

    op.create_index(
        "ix_outreach_drafts_connection_id",
        "outreach_drafts",
        ["connection_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_outreach_drafts_connection_id", table_name="outreach_drafts")
    with op.batch_alter_table("outreach_drafts") as batch:
        batch.drop_constraint(
            "fk_outreach_drafts_connection_id",
            type_="foreignkey",
        )
        batch.drop_constraint(
            "uq_outreach_drafts_job_contact_channel_connection",
            type_="unique",
        )
        batch.create_unique_constraint(
            "uq_outreach_drafts_job_contact_channel",
            ["job_id", "contact_id", "channel"],
        )
        batch.drop_column("connection_id")

    op.drop_index("ix_connections_linkedin_url", table_name="connections")
    op.drop_index("ix_connections_company_normalized", table_name="connections")
    op.drop_table("connections")
