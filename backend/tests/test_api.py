"""
Unit tests for the FastAPI REST API.

Uses httpx TestClient with an in-memory SQLite database so tests
are fast, isolated, and require no external services.

Key design: we use StaticPool + check_same_thread=False so that
all connections in the same process share a single in-memory DB.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.models import Base, Job, ScrapeScan
from backend.api.main import app
import backend.api.main as api_main


# ------------------------------------------------------------------
# Test DB setup — StaticPool ensures all connections share one DB
# ------------------------------------------------------------------

_test_engine = create_engine(
    "sqlite:///:memory:",
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_test_engine)
_TestSession = sessionmaker(bind=_test_engine)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

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


def _seed_jobs(session, count: int = 10) -> list[Job]:
    """Insert a variety of test jobs into the database."""
    jobs = []
    companies = [
        ("Razorpay", "fintech"),
        ("PhonePe", "fintech"),
        ("HDFC Bank", "bank"),
        ("ICICI Bank", "bank"),
        ("Bajaj Finance", "nbfc"),
        ("Stripe India", "fintech"),
        ("Paytm", "fintech"),
        ("SBI", "bank"),
        ("Groww", "fintech"),
        ("CRED", "fintech"),
    ]
    verdicts = ["STRONG_FIT", "GOOD_FIT", "MODERATE_FIT", "WEAK_FIT", "POOR_FIT"]
    priorities = ["APPLY_NOW", "REVIEW_FIRST", "SKIP"]

    for i in range(min(count, len(companies))):
        company_name, company_type = companies[i]
        score = 90 - (i * 8)  # 90, 82, 74, 66, ...
        is_scored = i < 8  # Leave last 2 unscored
        job = _make_job(
            title=f"Product Manager - {company_name}",
            company=company_name,
            dedup_hash=f"seed_hash_{i}",
            company_type=company_type,
            relevancy_score=score if is_scored else None,
            skills_match_score=round(score * 0.3, 1) if is_scored else None,
            domain_fit_score=round(score * 0.25, 1) if is_scored else None,
            experience_match_score=round(score * 0.2, 1) if is_scored else None,
            seniority_match_score=round(score * 0.15, 1) if is_scored else None,
            recency_score=round(score * 0.1, 1) if is_scored else None,
            verdict=verdicts[i % len(verdicts)] if is_scored else None,
            apply_priority=priorities[i % len(priorities)] if is_scored else None,
            score_reasoning=f"Good fit for {company_name}" if is_scored else None,
            missing_skills="SQL" if is_scored else None,
            description=f"Build products at {company_name}.",
            job_url=f"https://{company_name.lower().replace(' ', '')}.com/careers",
        )
        session.add(job)
        jobs.append(job)
    session.commit()
    for j in jobs:
        session.refresh(j)
    return jobs


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _patch_db():
    """
    Point the API at our shared in-memory test database and
    clean up all rows between tests.
    """
    # Override the module-level globals so _get_session() uses our engine
    api_main._engine = _test_engine
    api_main._SessionFactory = _TestSession

    yield

    # Truncate tables (keep schema)
    session = _TestSession()
    try:
        session.execute(text("DELETE FROM jobs"))
        session.execute(text("DELETE FROM scrape_scans"))
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client():
    """FastAPI test client."""
    return TestClient(app)


@pytest.fixture
def seeded_db():
    """Seed the test DB with 10 jobs and return them."""
    session = _TestSession()
    try:
        jobs = _seed_jobs(session)
    finally:
        session.close()
    return jobs


# ==================================================================
# Tests: Health Check
# ==================================================================

class TestHealthCheck:
    def test_health_returns_ok(self, client):
        """GET /api/health returns status ok."""
        resp = client.get("/api/health")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}


# ==================================================================
# Tests: List Jobs
# ==================================================================

class TestListJobs:
    def test_empty_db(self, client):
        """Empty database returns total 0 and empty list."""
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["jobs"] == []
        assert data["page"] == 1
        assert data["total_pages"] == 1

    def test_returns_seeded_jobs(self, client, seeded_db):
        """Returns all seeded jobs with default pagination."""
        resp = client.get("/api/jobs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 10
        assert len(data["jobs"]) == 10
        assert data["page"] == 1

    def test_pagination(self, client, seeded_db):
        """Pagination returns correct slice of results."""
        resp = client.get("/api/jobs?page=1&page_size=3")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["jobs"]) == 3
        assert data["total"] == 10
        assert data["page"] == 1
        assert data["total_pages"] == 4  # ceil(10/3) = 4

    def test_pagination_page_2(self, client, seeded_db):
        """Page 2 returns different jobs than page 1."""
        resp1 = client.get("/api/jobs?page=1&page_size=3")
        resp2 = client.get("/api/jobs?page=2&page_size=3")
        jobs1 = {j["id"] for j in resp1.json()["jobs"]}
        jobs2 = {j["id"] for j in resp2.json()["jobs"]}
        assert jobs1.isdisjoint(jobs2), "Pages should not overlap"

    def test_filter_priority(self, client, seeded_db):
        """Filter by apply_priority returns only matching jobs."""
        resp = client.get("/api/jobs?priority=APPLY_NOW")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for job in data["jobs"]:
            assert job["apply_priority"] == "APPLY_NOW"

    def test_filter_company_type(self, client, seeded_db):
        """Filter by company_type returns only matching jobs."""
        resp = client.get("/api/jobs?company_type=bank")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for job in data["jobs"]:
            assert job["company_type"] == "bank"

    def test_filter_score_range(self, client, seeded_db):
        """Score range filter works correctly."""
        resp = client.get("/api/jobs?min_score=50&max_score=80")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for job in data["jobs"]:
            assert 50 <= job["relevancy_score"] <= 80

    def test_search_by_company(self, client, seeded_db):
        """Search finds jobs by company name."""
        resp = client.get("/api/jobs?search=Razorpay")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1
        assert any("Razorpay" in j["company"] for j in data["jobs"])

    def test_search_by_title(self, client, seeded_db):
        """Search finds jobs by title."""
        resp = client.get("/api/jobs?search=Product Manager")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_sort_by_title_asc(self, client, seeded_db):
        """Sort by title ascending returns alphabetical order."""
        resp = client.get("/api/jobs?sort_by=title&sort_dir=asc")
        assert resp.status_code == 200
        titles = [j["title"] for j in resp.json()["jobs"]]
        assert titles == sorted(titles)

    def test_scored_only(self, client, seeded_db):
        """scored_only=true excludes unscored jobs."""
        resp = client.get("/api/jobs?scored_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 8
        for job in data["jobs"]:
            assert job["relevancy_score"] is not None

    def test_filter_verdict(self, client, seeded_db):
        """Filter by verdict returns only matching jobs."""
        resp = client.get("/api/jobs?verdict=STRONG_FIT")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for job in data["jobs"]:
            assert job["verdict"] == "STRONG_FIT"

    def test_combined_filters(self, client, seeded_db):
        """Multiple filters can be combined."""
        resp = client.get("/api/jobs?company_type=fintech&scored_only=true")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] > 0
        for job in data["jobs"]:
            assert job["company_type"] == "fintech"
            assert job["relevancy_score"] is not None


# ==================================================================
# Tests: Get Single Job
# ==================================================================

class TestGetJob:
    def test_get_existing_job(self, client, seeded_db):
        """GET /api/jobs/{id} returns the correct job."""
        job_id = seeded_db[0].id
        resp = client.get(f"/api/jobs/{job_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == job_id
        assert data["company"] == "Razorpay"

    def test_get_job_not_found(self, client):
        """GET /api/jobs/9999 returns 404."""
        resp = client.get("/api/jobs/9999")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_response_includes_all_fields(self, client, seeded_db):
        """Response includes scoring, classification, and timestamp fields."""
        job_id = seeded_db[0].id
        resp = client.get(f"/api/jobs/{job_id}")
        data = resp.json()
        expected_fields = [
            "id", "title", "company", "location", "source_portal",
            "relevancy_score", "verdict", "apply_priority",
            "company_type", "status", "date_scraped",
        ]
        for field in expected_fields:
            assert field in data, f"Missing field: {field}"


# ==================================================================
# Tests: R2 status enum
# ==================================================================

class TestJobStatus:
    def test_default_status_is_new(self, client, seeded_db):
        job_id = seeded_db[0].id
        body = client.get(f"/api/jobs/{job_id}").json()
        assert body["status"] == "new"

    def test_patch_status_round_trip(self, client, seeded_db):
        job_id = seeded_db[0].id
        resp = client.patch(f"/api/jobs/{job_id}", json={"status": "interviewing"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "interviewing"

    def test_patch_status_rejects_unknown(self, client, seeded_db):
        job_id = seeded_db[0].id
        resp = client.patch(f"/api/jobs/{job_id}", json={"status": "ghosted"})
        assert resp.status_code == 400

    def test_patch_status_404(self, client):
        resp = client.patch("/api/jobs/9999", json={"status": "saved"})
        assert resp.status_code == 404

    def test_default_list_excludes_hidden_and_rejected(self, client, seeded_db):
        # Hide one, reject another.
        client.patch(f"/api/jobs/{seeded_db[0].id}", json={"status": "hidden"})
        client.patch(f"/api/jobs/{seeded_db[1].id}", json={"status": "rejected"})

        body = client.get("/api/jobs").json()
        ids = {j["id"] for j in body["jobs"]}
        assert seeded_db[0].id not in ids
        assert seeded_db[1].id not in ids

    def test_status_filter_value_returns_only_that_state(self, client, seeded_db):
        client.patch(f"/api/jobs/{seeded_db[0].id}", json={"status": "applied"})
        client.patch(f"/api/jobs/{seeded_db[1].id}", json={"status": "applied"})

        body = client.get("/api/jobs?status=applied").json()
        ids = {j["id"] for j in body["jobs"]}
        assert ids == {seeded_db[0].id, seeded_db[1].id}

    def test_status_filter_all_includes_hidden(self, client, seeded_db):
        client.patch(f"/api/jobs/{seeded_db[0].id}", json={"status": "hidden"})
        body = client.get("/api/jobs?status=all").json()
        ids = {j["id"] for j in body["jobs"]}
        assert seeded_db[0].id in ids

# ==================================================================
# Tests: Stats
# ==================================================================

class TestStats:
    def test_stats_empty_db(self, client):
        """Stats with no jobs returns sensible defaults."""
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_jobs"] == 0
        assert data["scored_jobs"] == 0
        assert data["avg_score"] == 0.0
        assert data["applied_count"] == 0

    def test_stats_with_data(self, client, seeded_db):
        """Stats returns correct aggregates for seeded data."""
        resp = client.get("/api/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_jobs"] == 10
        assert data["scored_jobs"] == 8
        assert data["unscored_jobs"] == 2
        assert data["avg_score"] > 0
        assert data["max_score"] == 90.0
        assert data["min_score"] > 0

    def test_stats_priority_counts(self, client, seeded_db):
        """Stats includes priority breakdown."""
        resp = client.get("/api/stats")
        data = resp.json()
        total_priority = (
            data["apply_now_count"]
            + data["review_first_count"]
            + data["skip_count"]
        )
        assert total_priority == 8

    def test_stats_company_type_counts(self, client, seeded_db):
        """Stats includes company type breakdown."""
        resp = client.get("/api/stats")
        data = resp.json()
        assert data["fintech_count"] >= 1
        assert data["bank_count"] >= 1

    def test_stats_by_verdict_breakdown(self, client, seeded_db):
        """Stats includes verdict breakdown list."""
        resp = client.get("/api/stats")
        data = resp.json()
        assert isinstance(data["by_verdict"], list)
        assert len(data["by_verdict"]) > 0
        for item in data["by_verdict"]:
            assert "verdict" in item
            assert "count" in item

    def test_stats_by_company_type_breakdown(self, client, seeded_db):
        """Stats includes company type breakdown list."""
        resp = client.get("/api/stats")
        data = resp.json()
        assert isinstance(data["by_company_type"], list)
        for item in data["by_company_type"]:
            assert "company_type" in item
            assert "count" in item

    def test_stats_by_priority_breakdown(self, client, seeded_db):
        """Stats includes priority breakdown list."""
        resp = client.get("/api/stats")
        data = resp.json()
        assert isinstance(data["by_priority"], list)

    def test_stats_applied_count(self, client, seeded_db):
        """Applied count updates after status patch."""
        resp = client.get("/api/stats")
        assert resp.json()["applied_count"] == 0

        job_id = seeded_db[0].id
        client.patch(f"/api/jobs/{job_id}", json={"status": "applied"})

        resp = client.get("/api/stats")
        assert resp.json()["applied_count"] == 1

    def test_stats_uses_few_queries(self, client, seeded_db):
        """
        Regression test: /api/stats should use ≤5 SELECT queries.
        The pre-optimization version fired ~10 separate COUNTs.
        """
        select_count = 0

        def _on_statement(conn, cursor, statement, parameters, context, executemany):
            nonlocal select_count
            if statement.strip().upper().startswith("SELECT"):
                select_count += 1

        event.listen(_test_engine, "before_cursor_execute", _on_statement)
        try:
            resp = client.get("/api/stats")
        finally:
            event.remove(_test_engine, "before_cursor_execute", _on_statement)

        assert resp.status_code == 200
        assert select_count <= 5, f"Expected ≤5 SELECTs, got {select_count}"


# ==================================================================
# Phase 6.5 — Scheduler status endpoint
# ==================================================================

class TestSchedulerStatus:
    """`GET /api/scheduler/status` surfaces the most recent scrape scan and
    the latest scored job so the dashboard can show whether the background
    worker is alive."""

    def test_empty_db_returns_null_timestamps(self, client):
        resp = client.get("/api/scheduler/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["last_scrape_at"] is None
        assert body["last_score_at"] is None
        assert body["scored_jobs_last_24h"] == 0

    def test_last_scrape_reflects_most_recent_scan(self, client):
        session = _TestSession()
        try:
            old = ScrapeScan(
                engine="jobspy",
                status="completed",
                jobs_found=5,
                jobs_new=2,
                jobs_duplicate=3,
                started_at=datetime.now(timezone.utc) - timedelta(hours=10),
            )
            new = ScrapeScan(
                engine="apify",
                status="completed",
                jobs_found=8,
                jobs_new=6,
                jobs_duplicate=2,
                started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
            session.add_all([old, new])
            session.commit()
        finally:
            session.close()

        resp = client.get("/api/scheduler/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["last_scrape_at"] is not None
        assert body["last_scrape_status"] == "completed"
        # "new" record has jobs_new=6 — it should win over "old".
        assert body["last_scrape_new_jobs"] == 6

    def test_scored_last_24h_excludes_older_rows(self, client):
        now = datetime.now(timezone.utc)
        session = _TestSession()
        try:
            fresh = _make_job(
                company="FreshCo",
                dedup_hash="fresh1",
                relevancy_score=80.0,
                date_scored=now - timedelta(hours=2),
            )
            stale = _make_job(
                company="StaleCo",
                dedup_hash="stale1",
                relevancy_score=80.0,
                date_scored=now - timedelta(hours=48),
            )
            unscored = _make_job(
                company="NoScore",
                dedup_hash="nos1",
                relevancy_score=None,
                date_scored=None,
            )
            session.add_all([fresh, stale, unscored])
            session.commit()
        finally:
            session.close()

        resp = client.get("/api/scheduler/status")
        body = resp.json()
        assert body["scored_jobs_last_24h"] == 1
        assert body["last_score_at"] is not None

    def test_endpoint_is_unauthenticated(self, client, monkeypatch):
        """Status is read-only, should not require X-API-Key even in prod."""
        monkeypatch.setattr(api_main, "API_KEY", "secret")
        monkeypatch.setattr(api_main, "IS_PRODUCTION", True)
        resp = client.get("/api/scheduler/status")
        assert resp.status_code == 200


# ==================================================================
# Tests: API-key auth on mutation endpoints
# ==================================================================

class TestApiKeyAuth:
    """
    Verify the X-API-Key dependency on /api/scrape, /api/score and
    PATCH /api/jobs/{id}. Default test env has API_KEY unset AND
    IS_PRODUCTION=False, so auth falls through — which we already exercised
    in the mutation tests above. Here we cover the three other states:

      1. API_KEY set, request missing header → 401
      2. API_KEY set, request wrong header   → 401
      3. API_KEY set, request correct header → 200
      4. API_KEY unset + IS_PRODUCTION=True  → 503 (fail closed)
    """

    def _patch_key(self, monkeypatch, key: str, production: bool = False):
        monkeypatch.setattr(api_main, "API_KEY", key)
        monkeypatch.setattr(api_main, "IS_PRODUCTION", production)

    def test_missing_key_rejected(self, client, seeded_db, monkeypatch):
        self._patch_key(monkeypatch, "secret-abc")
        job_id = seeded_db[0].id
        resp = client.patch(f"/api/jobs/{job_id}", json={"status": "applied"})
        assert resp.status_code == 401

    def test_wrong_key_rejected(self, client, seeded_db, monkeypatch):
        self._patch_key(monkeypatch, "secret-abc")
        job_id = seeded_db[0].id
        resp = client.patch(
            f"/api/jobs/{job_id}",
            json={"status": "applied"},
            headers={"X-API-Key": "wrong"},
        )
        assert resp.status_code == 401

    def test_correct_key_accepted(self, client, seeded_db, monkeypatch):
        self._patch_key(monkeypatch, "secret-abc")
        job_id = seeded_db[0].id
        resp = client.patch(
            f"/api/jobs/{job_id}",
            json={"status": "applied"},
            headers={"X-API-Key": "secret-abc"},
        )
        assert resp.status_code == 200

    def test_production_without_key_fails_closed(
        self, client, seeded_db, monkeypatch
    ):
        """Missing API_KEY in production must block mutations (503)."""
        self._patch_key(monkeypatch, "", production=True)
        job_id = seeded_db[0].id
        resp = client.patch(f"/api/jobs/{job_id}", json={"status": "applied"})
        assert resp.status_code == 503

    def test_dev_mode_without_key_is_open(self, client, seeded_db, monkeypatch):
        """Dev convenience: empty API_KEY + not production → auth disabled."""
        self._patch_key(monkeypatch, "", production=False)
        job_id = seeded_db[0].id
        resp = client.patch(f"/api/jobs/{job_id}", json={"status": "applied"})
        assert resp.status_code == 200

    def test_read_endpoints_never_require_key(self, client, seeded_db, monkeypatch):
        """GETs must stay open regardless of auth config."""
        self._patch_key(monkeypatch, "secret-abc")
        assert client.get("/api/health").status_code == 200
        assert client.get("/api/jobs").status_code == 200
        assert client.get("/api/stats").status_code == 200
