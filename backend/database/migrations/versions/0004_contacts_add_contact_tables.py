"""add_contact_tables

Phase 7: hiring-manager / recruiter discovery.

Adds two tables:
    - contacts        — one row per discovered person (recruiter, HM, referral),
                        deduplicated on linkedin_url.
    - job_contacts    — many-to-many link between jobs and contacts, with the
                        source provider that surfaced the link + a confidence
                        score so we can rank suggestions per job.

Indexes are added for the hot lookups:
    - contacts.company + role_type (enrichment pipeline queries the 30-day
      cache by company; filtering by role_type narrows to HM vs recruiter)
    - contacts.last_enriched_at (cache-freshness check)
    - job_contacts (job_id, contact_id) unique — a single link per pair.

Revision ID: 0004_contacts
Revises: 0003_tier
Create Date: 2026-04-21
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0004_contacts"
down_revision: Union[str, None] = "0003_tier"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=True),
        sa.Column("company", sa.String(length=300), nullable=False),
        sa.Column("linkedin_url", sa.String(length=500), nullable=True),
        sa.Column("email", sa.String(length=300), nullable=True),
        sa.Column("role_type", sa.String(length=20), nullable=False),  # hm | recruiter | referral
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("source_provider", sa.String(length=30), nullable=False),  # apollo | hunter | linkedin_apify | manual
        sa.Column("raw_payload", sa.Text(), nullable=True),  # JSON blob from provider for debugging
        sa.Column("last_enriched_at", sa.DateTime(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.UniqueConstraint("linkedin_url", name="uq_contacts_linkedin_url"),
    )
    op.create_index("ix_contacts_company", "contacts", ["company"], unique=False)
    op.create_index(
        "ix_contacts_company_role",
        "contacts",
        ["company", "role_type"],
        unique=False,
    )
    op.create_index(
        "ix_contacts_last_enriched_at",
        "contacts",
        ["last_enriched_at"],
        unique=False,
    )

    op.create_table(
        "job_contacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("job_id", sa.Integer(), nullable=False),
        sa.Column("contact_id", sa.Integer(), nullable=False),
        sa.Column("provider", sa.String(length=30), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("job_id", "contact_id", name="uq_job_contacts_pair"),
    )
    op.create_index("ix_job_contacts_job_id", "job_contacts", ["job_id"], unique=False)
    op.create_index(
        "ix_job_contacts_contact_id", "job_contacts", ["contact_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_job_contacts_contact_id", table_name="job_contacts")
    op.drop_index("ix_job_contacts_job_id", table_name="job_contacts")
    op.drop_table("job_contacts")

    op.drop_index("ix_contacts_last_enriched_at", table_name="contacts")
    op.drop_index("ix_contacts_company_role", table_name="contacts")
    op.drop_index("ix_contacts_company", table_name="contacts")
    op.drop_table("contacts")
