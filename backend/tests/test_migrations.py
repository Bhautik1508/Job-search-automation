"""
Alembic migration smoke tests.

Guarantees that `alembic upgrade head` from an empty DB produces a schema
that (a) matches the SQLAlchemy models, (b) includes all hot-path indexes,
and (c) is idempotent to re-running.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
ALEMBIC_INI = PROJECT_ROOT / "alembic.ini"


def _make_config(db_url: str) -> Config:
    cfg = Config(str(ALEMBIC_INI))
    cfg.set_main_option("sqlalchemy.url", db_url)
    # Make paths absolute so alembic works regardless of cwd.
    cfg.set_main_option(
        "script_location",
        str(PROJECT_ROOT / "backend" / "database" / "migrations"),
    )
    return cfg


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "migrations.db"
    url = f"sqlite:///{db_path}"
    # env.py reads DATABASE_URL — override it for the duration of the test.
    monkeypatch.setenv("DATABASE_URL", url)
    return url


class TestMigrations:
    def test_upgrade_head_from_empty(self, fresh_db):
        command.upgrade(_make_config(fresh_db), "head")

        engine = create_engine(fresh_db)
        tables = set(inspect(engine).get_table_names())
        assert {"jobs", "scrape_scans", "alembic_version"}.issubset(tables)

    def test_upgrade_head_is_idempotent(self, fresh_db):
        cfg = _make_config(fresh_db)
        command.upgrade(cfg, "head")
        # Second upgrade should be a no-op (not raise).
        command.upgrade(cfg, "head")

    def test_hot_indexes_present(self, fresh_db):
        command.upgrade(_make_config(fresh_db), "head")
        engine = create_engine(fresh_db)
        index_names = {ix["name"] for ix in inspect(engine).get_indexes("jobs")}
        expected = {
            "ix_jobs_relevancy_score",
            "ix_jobs_apply_priority",
            "ix_jobs_company_type",
            "ix_jobs_verdict",
            "ix_jobs_date_scraped",
            "ix_jobs_status",
        }
        missing = expected - index_names
        assert not missing, f"Missing indexes: {missing}"

    def test_downgrade_removes_indexes(self, fresh_db):
        cfg = _make_config(fresh_db)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0001_baseline")

        engine = create_engine(fresh_db)
        index_names = {ix["name"] for ix in inspect(engine).get_indexes("jobs")}
        hot = {
            "ix_jobs_relevancy_score",
            "ix_jobs_apply_priority",
            "ix_jobs_company_type",
            "ix_jobs_verdict",
            "ix_jobs_date_scraped",
            "ix_jobs_status",
        }
        assert not (hot & index_names), "Hot indexes should be gone after downgrade"

    def test_models_match_migrated_schema(self, fresh_db):
        """Columns declared on Job/ScrapeScan/Contact/JobContact/OutreachDraft exist in the migrated DB."""
        from backend.database.models import (
            Contact, Job, JobContact, OutreachDraft, ScrapeScan,
        )

        command.upgrade(_make_config(fresh_db), "head")
        engine = create_engine(fresh_db)
        insp = inspect(engine)

        for model in (Job, ScrapeScan, Contact, JobContact, OutreachDraft):
            db_cols = {c["name"] for c in insp.get_columns(model.__tablename__)}
            model_cols = {c.name for c in model.__table__.columns}
            missing = model_cols - db_cols
            assert not missing, f"{model.__tablename__} missing cols: {missing}"

    def test_phase_7_contact_tables_present(self, fresh_db):
        """Phase 7 migration adds contacts + job_contacts tables with hot indexes."""
        command.upgrade(_make_config(fresh_db), "head")
        engine = create_engine(fresh_db)
        insp = inspect(engine)

        tables = set(insp.get_table_names())
        assert {"contacts", "job_contacts"}.issubset(tables)

        contact_indexes = {ix["name"] for ix in insp.get_indexes("contacts")}
        assert {
            "ix_contacts_company",
            "ix_contacts_company_role",
            "ix_contacts_last_enriched_at",
        }.issubset(contact_indexes)

        job_contact_indexes = {ix["name"] for ix in insp.get_indexes("job_contacts")}
        assert {
            "ix_job_contacts_job_id",
            "ix_job_contacts_contact_id",
        }.issubset(job_contact_indexes)

    def test_phase_7_downgrade_drops_contact_tables(self, fresh_db):
        cfg = _make_config(fresh_db)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0003_tier")

        engine = create_engine(fresh_db)
        tables = set(inspect(engine).get_table_names())
        assert "contacts" not in tables
        assert "job_contacts" not in tables

    def test_phase_8_outreach_table_present(self, fresh_db):
        """Phase 8 migration adds outreach_drafts with expected indexes + unique key."""
        command.upgrade(_make_config(fresh_db), "head")
        engine = create_engine(fresh_db)
        insp = inspect(engine)

        assert "outreach_drafts" in set(insp.get_table_names())

        indexes = {ix["name"] for ix in insp.get_indexes("outreach_drafts")}
        assert {
            "ix_outreach_drafts_job_id",
            "ix_outreach_drafts_contact_id",
            "ix_outreach_drafts_status",
        }.issubset(indexes)

        uniques = {uq["name"] for uq in insp.get_unique_constraints("outreach_drafts")}
        # R4 widened the constraint to include connection_id; the legacy
        # name lives only in pre-R4 schemas.
        assert "uq_outreach_drafts_job_contact_channel_connection" in uniques

    def test_phase_8_downgrade_drops_outreach_table(self, fresh_db):
        cfg = _make_config(fresh_db)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0004_contacts")

        engine = create_engine(fresh_db)
        assert "outreach_drafts" not in set(inspect(engine).get_table_names())

    def test_phase_r3_case_study_columns_present(self, fresh_db):
        """0007 adds case_study_link / case_study_attachment to outreach_drafts."""
        command.upgrade(_make_config(fresh_db), "head")
        engine = create_engine(fresh_db)
        cols = {c["name"] for c in inspect(engine).get_columns("outreach_drafts")}
        assert "case_study_link" in cols
        assert "case_study_attachment" in cols

    def test_phase_r3_downgrade_drops_case_study_columns(self, fresh_db):
        cfg = _make_config(fresh_db)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0006_status")
        engine = create_engine(fresh_db)
        cols = {c["name"] for c in inspect(engine).get_columns("outreach_drafts")}
        assert "case_study_link" not in cols
        assert "case_study_attachment" not in cols

    def test_phase_r4_connections_table_present(self, fresh_db):
        """0008 adds connections table + connection_id link on outreach_drafts."""
        command.upgrade(_make_config(fresh_db), "head")
        engine = create_engine(fresh_db)
        insp = inspect(engine)

        assert "connections" in set(insp.get_table_names())
        conn_indexes = {ix["name"] for ix in insp.get_indexes("connections")}
        assert {
            "ix_connections_company_normalized",
            "ix_connections_linkedin_url",
        }.issubset(conn_indexes)

        outreach_cols = {c["name"] for c in insp.get_columns("outreach_drafts")}
        assert "connection_id" in outreach_cols

    def test_phase_r4_downgrade_drops_connections(self, fresh_db):
        cfg = _make_config(fresh_db)
        command.upgrade(cfg, "head")
        command.downgrade(cfg, "0007_case_study")
        engine = create_engine(fresh_db)
        insp = inspect(engine)
        assert "connections" not in set(insp.get_table_names())
        outreach_cols = {c["name"] for c in insp.get_columns("outreach_drafts")}
        assert "connection_id" not in outreach_cols
