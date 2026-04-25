"""add_case_study_columns_to_outreach_drafts

Phase R3: outreach UX upgrade.

Adds two optional columns to `outreach_drafts` so a generated draft can
carry along the case-study artifact that should accompany it:

    case_study_link        — public URL (Notion, Medium, personal site)
    case_study_attachment  — relative path to a PDF in backend/portfolio/

Either, both, or neither may be set. Email channels can render the link
inline; LinkedIn channels surface "Attach this when you send" hints.

Revision ID: 0007_case_study
Revises: 0006_status
Create Date: 2026-04-25
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0007_case_study"
down_revision: Union[str, None] = "0006_status"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("outreach_drafts") as batch:
        batch.add_column(sa.Column("case_study_link", sa.String(length=500), nullable=True))
        batch.add_column(sa.Column("case_study_attachment", sa.String(length=500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("outreach_drafts") as batch:
        batch.drop_column("case_study_attachment")
        batch.drop_column("case_study_link")
