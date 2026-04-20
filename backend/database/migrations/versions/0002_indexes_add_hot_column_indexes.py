"""add_hot_column_indexes

Adds indexes on columns frequently used in filters, sorts, and GROUP BY
clauses by /api/jobs and /api/stats. Without these, every dashboard
request does full table scans — fine at 100 rows, painful at 10k+.

Revision ID: 0002_indexes
Revises: 0001_baseline
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op


revision: str = "0002_indexes"
down_revision: Union[str, None] = "0001_baseline"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_INDEXES = [
    ("ix_jobs_relevancy_score", ["relevancy_score"]),
    ("ix_jobs_apply_priority", ["apply_priority"]),
    ("ix_jobs_company_type", ["company_type"]),
    ("ix_jobs_verdict", ["verdict"]),
    ("ix_jobs_date_scraped", ["date_scraped"]),
    ("ix_jobs_applied_relevancy", ["applied", "relevancy_score"]),
]


def upgrade() -> None:
    for name, cols in _INDEXES:
        op.create_index(name, "jobs", cols, unique=False)


def downgrade() -> None:
    for name, _cols in reversed(_INDEXES):
        op.drop_index(name, table_name="jobs")
