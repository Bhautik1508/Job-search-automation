"""baseline_phase_1_to_4_schema

Captures the Phase 1–4 schema as it existed before Alembic was introduced.
On existing deployments, run `alembic stamp 0001_baseline` so this revision
is marked applied without re-running the CREATE TABLE statements. On a
fresh database, `alembic upgrade head` will build the schema from scratch.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-04-20
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "0001_baseline"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "jobs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("title", sa.String(length=500), nullable=False),
        sa.Column("company", sa.String(length=300), nullable=False),
        sa.Column("location", sa.String(length=300), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("job_url", sa.String(length=2000), nullable=True),
        sa.Column("source_portal", sa.String(length=50), nullable=False),
        sa.Column("source_engine", sa.String(length=20), nullable=False),
        sa.Column("external_id", sa.String(length=500), nullable=True),
        sa.Column("salary_min", sa.Float(), nullable=True),
        sa.Column("salary_max", sa.Float(), nullable=True),
        sa.Column("salary_currency", sa.String(length=10), nullable=True),
        sa.Column("experience_required", sa.String(length=100), nullable=True),
        sa.Column("skills", sa.Text(), nullable=True),
        sa.Column("job_type", sa.String(length=50), nullable=True),
        sa.Column("work_mode", sa.String(length=50), nullable=True),
        sa.Column("company_type", sa.String(length=30), nullable=True),
        sa.Column("relevancy_score", sa.Float(), nullable=True),
        sa.Column("skills_match_score", sa.Float(), nullable=True),
        sa.Column("domain_fit_score", sa.Float(), nullable=True),
        sa.Column("experience_match_score", sa.Float(), nullable=True),
        sa.Column("seniority_match_score", sa.Float(), nullable=True),
        sa.Column("recency_score", sa.Float(), nullable=True),
        sa.Column("verdict", sa.String(length=20), nullable=True),
        sa.Column("apply_priority", sa.String(length=20), nullable=True),
        sa.Column("score_reasoning", sa.Text(), nullable=True),
        sa.Column("missing_skills", sa.Text(), nullable=True),
        sa.Column("applied", sa.Boolean(), nullable=True),
        sa.Column("application_status", sa.String(length=30), nullable=True),
        sa.Column("date_posted", sa.DateTime(), nullable=True),
        sa.Column("date_scraped", sa.DateTime(), nullable=True),
        sa.Column("date_scored", sa.DateTime(), nullable=True),
        sa.Column("dedup_hash", sa.String(length=64), nullable=True),
        sa.UniqueConstraint("dedup_hash", name="uq_jobs_dedup_hash"),
    )
    op.create_index("ix_jobs_dedup_hash", "jobs", ["dedup_hash"], unique=False)

    op.create_table(
        "scrape_scans",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("engine", sa.String(length=20), nullable=False),
        sa.Column("portals", sa.String(length=200), nullable=True),
        sa.Column("search_term", sa.String(length=200), nullable=True),
        sa.Column("location", sa.String(length=200), nullable=True),
        sa.Column("jobs_found", sa.Integer(), nullable=True),
        sa.Column("jobs_new", sa.Integer(), nullable=True),
        sa.Column("jobs_duplicate", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=True),
        sa.Column("completed_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_table("scrape_scans")
    op.drop_index("ix_jobs_dedup_hash", table_name="jobs")
    op.drop_table("jobs")
