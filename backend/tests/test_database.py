"""
Unit tests for database models and CRUD operations.
Uses an in-memory SQLite database for isolation.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base, Job, ScrapeScan, init_db
from backend.database.crud import (
    insert_job,
    get_job_by_id,
    get_job_by_dedup_hash,
    get_all_jobs,
    bulk_insert_jobs,
    count_jobs,
    create_scrape_scan,
    complete_scrape_scan,
    get_recent_scans,
)


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture
def db_session():
    """Create a fresh in-memory SQLite database and session for each test."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _make_job(
    title: str = "Product Manager",
    company: str = "Razorpay",
    portal: str = "naukri",
    dedup_hash: str | None = None,
    **kwargs,
) -> Job:
    """Factory for test Job objects."""
    return Job(
        title=title,
        company=company,
        location="Bangalore",
        source_portal=portal,
        source_engine="test",
        dedup_hash=dedup_hash or f"hash_{title}_{company}_{portal}",
        date_scraped=datetime.now(timezone.utc),
        **kwargs,
    )


# ==================================================================
# Tests: Job Model
# ==================================================================

class TestJobModel:
    def test_create_job(self, db_session):
        """A job can be created and persisted."""
        job = _make_job()
        db_session.add(job)
        db_session.commit()
        assert job.id is not None
        assert job.title == "Product Manager"

    def test_repr(self, db_session):
        """Job __repr__ is informative."""
        job = _make_job()
        db_session.add(job)
        db_session.commit()
        r = repr(job)
        assert "Product Manager" in r
        assert "Razorpay" in r

    def test_defaults(self, db_session):
        """Default values are set correctly."""
        job = _make_job()
        db_session.add(job)
        db_session.commit()
        assert job.status == "new"
        assert job.relevancy_score is None
        assert job.application_status is None

    def test_unique_dedup_hash(self, db_session):
        """Inserting two jobs with the same dedup_hash should raise."""
        j1 = _make_job(dedup_hash="same_hash")
        j2 = _make_job(title="Another Role", dedup_hash="same_hash")
        db_session.add(j1)
        db_session.commit()
        db_session.add(j2)
        with pytest.raises(Exception):
            db_session.commit()
        db_session.rollback()

    def test_all_fields(self, db_session):
        """Job with all fields populated persists correctly."""
        job = _make_job(
            salary_min=1200000.0,
            salary_max=1800000.0,
            salary_currency="INR",
            experience_required="3-5 years",
            skills="product strategy, analytics, sql",
            job_type="full-time",
            work_mode="hybrid",
            company_type="fintech",
            description="Build amazing products at Razorpay.",
            job_url="https://razorpay.com/careers/pm",
        )
        db_session.add(job)
        db_session.commit()

        fetched = db_session.query(Job).filter(Job.id == job.id).first()
        assert fetched.salary_min == 1200000.0
        assert fetched.company_type == "fintech"
        assert fetched.skills == "product strategy, analytics, sql"


# ==================================================================
# Tests: ScrapeScan Model
# ==================================================================

class TestScrapeScanModel:
    def test_create_scan(self, db_session):
        """A scrape scan can be created."""
        scan = ScrapeScan(engine="test", portals="naukri,linkedin")
        db_session.add(scan)
        db_session.commit()
        assert scan.id is not None
        assert scan.status == "running"

    def test_repr(self, db_session):
        """ScrapeScan repr shows useful info."""
        scan = ScrapeScan(engine="orchestrator")
        db_session.add(scan)
        db_session.commit()
        r = repr(scan)
        assert "orchestrator" in r


# ==================================================================
# Tests: CRUD Operations
# ==================================================================

class TestCrudJobs:
    def test_insert_and_fetch(self, db_session):
        """Insert a job and fetch it by ID."""
        job = _make_job()
        inserted = insert_job(db_session, job)
        assert inserted.id is not None

        fetched = get_job_by_id(db_session, inserted.id)
        assert fetched is not None
        assert fetched.title == "Product Manager"

    def test_fetch_nonexistent(self, db_session):
        """Fetching a non-existent job returns None."""
        assert get_job_by_id(db_session, 99999) is None

    def test_get_by_dedup_hash(self, db_session):
        """Fetch a job by its dedup hash."""
        job = _make_job(dedup_hash="unique_hash_123")
        insert_job(db_session, job)
        found = get_job_by_dedup_hash(db_session, "unique_hash_123")
        assert found is not None
        assert found.company == "Razorpay"

    def test_get_by_dedup_hash_not_found(self, db_session):
        """Missing hash returns None."""
        assert get_job_by_dedup_hash(db_session, "doesnt_exist") is None

    def test_get_all_jobs(self, db_session):
        """get_all_jobs returns jobs in descending scrape order."""
        for i in range(5):
            insert_job(db_session, _make_job(
                title=f"PM {i}", company=f"Company {i}", dedup_hash=f"hash_{i}",
            ))
        jobs = get_all_jobs(db_session)
        assert len(jobs) == 5

    def test_get_all_jobs_limit(self, db_session):
        """Limit parameter works."""
        for i in range(10):
            insert_job(db_session, _make_job(dedup_hash=f"limit_hash_{i}"))
        jobs = get_all_jobs(db_session, limit=3)
        assert len(jobs) == 3

    def test_count_jobs(self, db_session):
        """Count returns the total number of jobs."""
        assert count_jobs(db_session) == 0
        for i in range(3):
            insert_job(db_session, _make_job(dedup_hash=f"count_hash_{i}"))
        assert count_jobs(db_session) == 3

    def test_bulk_insert_jobs(self, db_session):
        """Bulk insert adds new jobs and skips duplicates."""
        jobs = [
            _make_job(title="PM A", dedup_hash="bulk_a"),
            _make_job(title="PM B", dedup_hash="bulk_b"),
            _make_job(title="PM C", dedup_hash="bulk_c"),
        ]
        inserted = bulk_insert_jobs(db_session, jobs)
        assert inserted == 3
        assert count_jobs(db_session) == 3

    def test_bulk_insert_skips_existing(self, db_session):
        """Bulk insert correctly skips jobs with existing dedup hashes."""
        # First insert
        insert_job(db_session, _make_job(dedup_hash="existing_hash"))

        # Bulk insert with one existing and one new
        jobs = [
            _make_job(title="Existing", dedup_hash="existing_hash"),
            _make_job(title="New", dedup_hash="new_hash"),
        ]
        inserted = bulk_insert_jobs(db_session, jobs)
        assert inserted == 1
        assert count_jobs(db_session) == 2

    def test_bulk_insert_dedupes_within_batch(self, db_session):
        """Duplicates in the same batch collapse to a single row."""
        jobs = [
            _make_job(title="Dup", dedup_hash="dup_hash"),
            _make_job(title="Dup", dedup_hash="dup_hash"),
            _make_job(title="Other", dedup_hash="other_hash"),
        ]
        inserted = bulk_insert_jobs(db_session, jobs)
        assert inserted == 2
        assert count_jobs(db_session) == 2

    def test_bulk_insert_empty_list(self, db_session):
        """Empty input returns 0 and does not hit the DB."""
        assert bulk_insert_jobs(db_session, []) == 0
        assert count_jobs(db_session) == 0

    def test_bulk_insert_uses_single_lookup_query(self, db_session):
        """
        Regression test: existing-hash lookup should be a single batched
        query, not one-per-job. We assert by counting SELECTs fired.
        """
        from sqlalchemy import event

        # Seed an existing row so the lookup path is exercised.
        insert_job(db_session, _make_job(dedup_hash="seeded"))

        select_count = 0

        def _on_statement(conn, cursor, statement, parameters, context, executemany):
            nonlocal select_count
            if statement.strip().upper().startswith("SELECT"):
                select_count += 1

        engine = db_session.get_bind()
        event.listen(engine, "before_cursor_execute", _on_statement)
        try:
            jobs = [
                _make_job(title=f"J{i}", dedup_hash=f"batch_{i}") for i in range(20)
            ]
            bulk_insert_jobs(db_session, jobs)
        finally:
            event.remove(engine, "before_cursor_execute", _on_statement)

        # At most one SELECT for the dedup-hash lookup. (Not N=20.)
        assert select_count <= 2, f"Expected batched lookup, got {select_count} SELECTs"


# ==================================================================
# Tests: CRUD ScrapeScan
# ==================================================================

class TestCrudScrapeScan:
    def test_create_and_complete_scan(self, db_session):
        """Create a scan, then mark it complete."""
        scan = create_scrape_scan(db_session, engine="jobspy", portals="naukri,linkedin")
        assert scan.status == "running"
        assert scan.id is not None

        complete_scrape_scan(
            db_session, scan,
            jobs_found=50, jobs_new=40, jobs_duplicate=10,
            status="completed",
        )
        assert scan.status == "completed"
        assert scan.jobs_found == 50
        assert scan.jobs_new == 40
        assert scan.completed_at is not None

    def test_failed_scan(self, db_session):
        """A scan can be marked as failed with an error message."""
        scan = create_scrape_scan(db_session, engine="apify")
        complete_scrape_scan(
            db_session, scan,
            jobs_found=0, jobs_new=0, jobs_duplicate=0,
            status="failed", error_message="Rate limit exceeded",
        )
        assert scan.status == "failed"
        assert "Rate limit" in scan.error_message

    def test_get_recent_scans(self, db_session):
        """Recent scans are returned in descending order."""
        for i in range(5):
            create_scrape_scan(db_session, engine=f"engine_{i}")
        scans = get_recent_scans(db_session, limit=3)
        assert len(scans) == 3


# ==================================================================
# Tests: get_engine Postgres-readiness
# ==================================================================

class TestGetEngine:
    def test_sqlite_engine_skips_pool_pre_ping(self):
        """SQLite doesn't need pool_pre_ping — keep the engine kwargs minimal."""
        from backend.database.models import get_engine

        engine = get_engine("sqlite:///:memory:")
        # SQLAlchemy exposes pool_pre_ping on the engine's pool as `_pre_ping`.
        assert getattr(engine.pool, "_pre_ping", False) is False

    def test_non_sqlite_engine_enables_pool_pre_ping(self, monkeypatch):
        """
        For Postgres/MySQL we must pre-ping to survive managed-DB idle timeouts.
        We can't actually connect to Postgres in CI (no driver installed), so we
        patch create_engine and assert get_engine passed pool_pre_ping=True.
        """
        from backend.database import models

        captured: dict = {}

        def fake_create_engine(url, **kwargs):
            captured["url"] = url
            captured["kwargs"] = kwargs
            return object()

        monkeypatch.setattr(models, "create_engine", fake_create_engine)
        models.get_engine("postgresql+psycopg2://u:p@localhost/nope")

        assert captured["kwargs"].get("pool_pre_ping") is True
        assert captured["kwargs"].get("echo") is False
