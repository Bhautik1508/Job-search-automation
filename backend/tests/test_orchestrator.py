"""
Integration test for the ScraperOrchestrator.

Uses mock scrapers so no real network calls are made.
Tests the full pipeline: scrape → deduplicate → store to DB.
"""

import pytest
from unittest.mock import MagicMock
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base, Job
from backend.scrapers.base_scraper import BaseScraper, RawJob
from backend.scrapers.scraper_orchestrator import ScraperOrchestrator
from backend.database.crud import count_jobs, get_all_jobs


# ------------------------------------------------------------------
# Mock scraper that returns canned data
# ------------------------------------------------------------------

class MockScraper(BaseScraper):
    """Scraper that returns pre-defined jobs for testing."""

    engine_name = "mock"

    def __init__(self, jobs: list[RawJob]):
        self._jobs = jobs

    def scrape(self, search_term, location, results_wanted=30, hours_old=72):
        return self._jobs


class EmptyScraper(BaseScraper):
    """Scraper that always returns nothing."""

    engine_name = "empty"

    def scrape(self, search_term, location, results_wanted=30, hours_old=72):
        return []


class FailingScraper(BaseScraper):
    """Scraper that always fails."""

    engine_name = "failing"

    def scrape(self, search_term, location, results_wanted=30, hours_old=72):
        raise RuntimeError("Simulated scraper failure")


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------

MOCK_JOBS = [
    RawJob(title="Product Manager", company="Razorpay", location="Bangalore",
           source_portal="naukri", source_engine="mock",
           description="Build payment products", job_url="https://razorpay.com/pm"),
    RawJob(title="Senior Product Manager", company="PhonePe", location="Bangalore",
           source_portal="linkedin", source_engine="mock",
           description="Lead UPI products", job_url="https://phonepe.com/pm"),
    RawJob(title="Product Manager - Lending", company="CRED", location="Mumbai",
           source_portal="indeed", source_engine="mock",
           description="Credit products", job_url="https://cred.club/pm"),
    RawJob(title="PM - Digital Banking", company="HDFC Bank", location="Mumbai",
           source_portal="naukri", source_engine="mock",
           description="Digital transformation", job_url="https://hdfc.com/pm"),
]

DUPLICATE_JOBS = [
    RawJob(title="Product Manager", company="Razorpay", location="Bangalore",
           source_portal="naukri", source_engine="mock1"),
    RawJob(title="Product Manager", company="Razorpay", location="Bangalore",
           source_portal="linkedin", source_engine="mock2"),
]


# ==================================================================
# Tests
# ==================================================================

class TestScraperOrchestrator:
    def test_full_pipeline(self):
        """Full pipeline: scrape → dedup → store works end-to-end."""
        orchestrator = ScraperOrchestrator(
            engines=[MockScraper(MOCK_JOBS)],
            search_terms=["Product Manager"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
        )
        result = orchestrator.run()

        assert result["status"] == "completed"
        assert result["total_raw"] == 4
        assert result["new_inserted"] == 4
        assert result["scan_id"] is not None

    def test_dedup_across_engines(self):
        """Duplicates across engines are caught."""
        engine1 = MockScraper([DUPLICATE_JOBS[0]])
        engine2 = MockScraper([DUPLICATE_JOBS[1]])

        orchestrator = ScraperOrchestrator(
            engines=[engine1, engine2],
            search_terms=["PM"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
        )
        result = orchestrator.run()

        # Fuzzy dedup should catch the duplicate
        assert result["new_inserted"] <= 1  # At most 1 after dedup

    def test_empty_scraper(self):
        """Orchestrator handles scrapers that return nothing."""
        orchestrator = ScraperOrchestrator(
            engines=[EmptyScraper()],
            search_terms=["PM"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
        )
        result = orchestrator.run()

        assert result["status"] == "completed"
        assert result["new_inserted"] == 0

    def test_multiple_runs_no_duplication(self):
        """Running the orchestrator twice doesn't create duplicates."""
        orchestrator = ScraperOrchestrator(
            engines=[MockScraper(MOCK_JOBS)],
            search_terms=["PM"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
        )

        r1 = orchestrator.run()
        r2 = orchestrator.run()

        assert r1["new_inserted"] == 4
        assert r2["new_inserted"] == 0
        assert r2["duplicates_skipped"] == 4

    def test_failing_scraper_doesnt_crash(self):
        """A failing scraper doesn't crash the whole pipeline."""
        orchestrator = ScraperOrchestrator(
            engines=[FailingScraper(), MockScraper(MOCK_JOBS)],
            search_terms=["PM"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
        )
        result = orchestrator.run()

        # The mock scraper's jobs should still be stored
        assert result["new_inserted"] == 4

    def test_scan_record_created(self):
        """A ScrapeScan record is created for each run."""
        orchestrator = ScraperOrchestrator(
            engines=[MockScraper(MOCK_JOBS[:2])],
            search_terms=["PM"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
        )
        result = orchestrator.run()
        assert result["scan_id"] is not None

    def test_raw_to_db_job_conversion(self):
        """RawJob → Job conversion preserves all fields."""
        raw = RawJob(
            title="Product Manager",
            company="Razorpay",
            location="Bangalore",
            description="Build things",
            job_url="https://razorpay.com",
            source_portal="naukri",
            source_engine="jobspy",
            salary_min=1500000,
            salary_max=2500000,
            salary_currency="INR",
            skills="SQL, analytics",
            job_type="full-time",
            work_mode="hybrid",
        )
        db_job = ScraperOrchestrator._raw_to_db_job(raw)

        assert db_job.title == "Product Manager"
        assert db_job.company == "Razorpay"
        assert db_job.salary_min == 1500000
        assert db_job.source_engine == "jobspy"
        assert db_job.dedup_hash is not None
        assert len(db_job.dedup_hash) == 64
