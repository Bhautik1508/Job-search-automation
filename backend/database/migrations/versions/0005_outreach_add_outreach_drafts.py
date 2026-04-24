"""add_outreach_drafts

Phase 8: AI-drafted outreach messages per (job, contact).

    - outreach_drafts (job_id, contact_id, channel, tone, subject, body,
                       attachments, status, model, created_at, updated_at)

A single (job, contact, channel) triple is unique — rerunning draft
generation updates the existing row instead of accumulating copies.

Status lifecycle: draft → sent → replied. Kept as a string so we can
add states without another migration.

Revision ID: 0005_outreach
Revises: 0004_contacts
Create Date: 2026-04-24
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0005_outreach"
down_revision: Union[str, None] = "0004_contacts"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "outreach_drafts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=30), nullable=False),   # linkedin_note | linkedin_inmail | email | referral_ask
        sa.Column("tone", sa.String(length=30), nullable=False),      # founder-pitch | peer-pm | recruiter-formal
        sa.Column("subject", sa.String(length=500), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("attachments", sa.Text(), nullable=True),           # JSON-encoded list of portfolio item IDs
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("model", sa.String(length=60), nullable=True),      # gemini model id used, for audit
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint(
            "job_id", "contact_id", "channel",
            name="uq_outreach_drafts_job_contact_channel",
        ),
    )
    op.create_index(
        "ix_outreach_drafts_job_id", "outreach_drafts", ["job_id"], unique=False,
    )
    op.create_index(
        "ix_outreach_drafts_contact_id", "outreach_drafts", ["contact_id"], unique=False,
    )
    op.create_index(
        "ix_outreach_drafts_status", "outreach_drafts", ["status"], unique=False,
    )


def downgrade() -> None:
    op.drop_index("ix_outreach_drafts_status", table_name="outreach_drafts")
    op.drop_index("ix_outreach_drafts_contact_id", table_name="outreach_drafts")
    op.drop_index("ix_outreach_drafts_job_id", table_name="outreach_drafts")
    op.drop_table("outreach_drafts")
