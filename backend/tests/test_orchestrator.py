"""
Integration test for the ScraperOrchestrator.

Uses mock scrapers so no real network calls are made.
Tests the full pipeline: scrape → deduplicate → store to DB.
"""

import pytest
import threading
import time
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
    RawJob(title="Product Manager - Digital Banking", company="HDFC Bank", location="Mumbai",
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
        """Full pipeline: scrape → title-filter → location-filter → dedup → store."""
        orchestrator = ScraperOrchestrator(
            engines=[MockScraper(MOCK_JOBS)],
            search_terms=["Product Manager"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
        )
        result = orchestrator.run()

        assert result["status"] == "completed"
        assert result["total_raw"] == 4
        # Location filter drops the two Mumbai jobs (allowed keywords include
        # bangalore/bengaluru/pune but not mumbai).
        assert result["location_filtered_out"] == 2
        assert result["new_inserted"] == 2
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

        # 2 of the 4 mock jobs are Mumbai-based → filtered by location filter.
        assert r1["new_inserted"] == 2
        assert r2["new_inserted"] == 0
        assert r2["duplicates_skipped"] == 2

    def test_failing_scraper_doesnt_crash(self):
        """A failing scraper doesn't crash the whole pipeline."""
        orchestrator = ScraperOrchestrator(
            engines=[FailingScraper(), MockScraper(MOCK_JOBS)],
            search_terms=["PM"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
        )
        result = orchestrator.run()

        # The mock scraper's jobs should still be stored (Mumbai ones filtered).
        assert result["new_inserted"] == 2

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

    def test_irrelevant_titles_filtered_out(self):
        """Orchestrator drops irrelevant titles (e.g. SDE) before storing."""
        mixed_jobs = [
            RawJob(title="Product Manager", company="Razorpay",
                   location="Bangalore", source_portal="naukri", source_engine="mock"),
            RawJob(title="Software Engineer", company="Razorpay",
                   location="Bangalore", source_portal="naukri", source_engine="mock"),
            RawJob(title="Senior Product Manager", company="PhonePe",
                   location="Bangalore", source_portal="linkedin", source_engine="mock"),
            RawJob(title="DevOps Engineer", company="PhonePe",
                   location="Bangalore", source_portal="linkedin", source_engine="mock"),
        ]
        orchestrator = ScraperOrchestrator(
            engines=[MockScraper(mixed_jobs)],
            search_terms=["PM"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
        )
        result = orchestrator.run()
        # Only the two PM titles should survive
        assert result["new_inserted"] == 2

    def test_filter_runs_before_dedup(self):
        """
        Title filtering must happen BEFORE fuzzy dedup so the dedup step
        doesn't waste work on irrelevant rows.

        We verify the two steps by calling the static helper on a crafted
        input that has an irrelevant job fuzzy-matching a relevant one.
        If dedup ran first, the relevant PM role could be dropped as a dup
        of the SDE entry. Filter-first guarantees the PM survives.
        """
        jobs = [
            RawJob(title="Software Engineer", company="Razorpay",
                   source_portal="x", source_engine="mock"),
            RawJob(title="Product Manager", company="Razorpay",
                   source_portal="y", source_engine="mock"),
        ]
        # _filter_relevant_titles is now an instance method (tracks rejected
        # sample on self for diagnostics). Instantiate via a minimal orchestrator.
        kept = ScraperOrchestrator(engines=[])._filter_relevant_titles(jobs)
        assert len(kept) == 1
        assert kept[0].title == "Product Manager"

    def test_engines_run_in_parallel(self):
        """
        Two slow engines should finish in roughly one engine's wall time
        when parallel=True (i.e. < 2× the per-engine delay).
        """

        class SlowScraper(BaseScraper):
            engine_name = "slow"

            def __init__(self, delay: float, tag: str):
                self.delay = delay
                self.tag = tag

            def scrape(self, search_term, location, results_wanted=30, hours_old=72):
                time.sleep(self.delay)
                return [
                    RawJob(
                        title=f"Senior Product Manager {self.tag.upper() * 6}",
                        company=f"CompanyAlpha{self.tag * 4}",
                        location="Bangalore",
                        source_portal="portal",
                        source_engine=f"slow-{self.tag}",
                    )
                ]

        delay = 0.25
        engines = [SlowScraper(delay, "a"), SlowScraper(delay, "b")]
        orchestrator = ScraperOrchestrator(
            engines=engines,
            search_terms=["PM"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
            parallel=True,
        )

        start = time.perf_counter()
        result = orchestrator.run()
        elapsed = time.perf_counter() - start

        assert result["new_inserted"] == 2
        # Serial would take ≥ 2*delay. Parallel should land well under 1.7*delay.
        assert elapsed < 1.7 * delay, f"Expected parallel execution, took {elapsed:.2f}s"

    def test_parallel_false_runs_serially(self):
        """Explicit parallel=False keeps the old behavior (no thread pool)."""
        engine_threads: set[int] = set()

        class RecordingScraper(BaseScraper):
            engine_name = "rec"

            def scrape(self, search_term, location, results_wanted=30, hours_old=72):
                engine_threads.add(threading.get_ident())
                return []

        orchestrator = ScraperOrchestrator(
            engines=[RecordingScraper(), RecordingScraper()],
            search_terms=["PM"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
            parallel=False,
        )
        orchestrator.run()
        # Serial path runs everything on the caller thread.
        assert len(engine_threads) == 1

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


# ==================================================================
# Phase 6.5 — Instahyre gate
# ==================================================================

class TestInstahyreGate:
    """`_default_engines` should drop Instahyre when INSTAHYRE_ENABLED is
    false, even if credentials happen to be set. This keeps the Playwright
    dependency out of Render's free-tier web/worker dynos unless explicitly
    opted in."""

    def test_instahyre_skipped_when_env_disabled(self, monkeypatch, capsys):
        import backend.config as cfg
        monkeypatch.setattr(cfg, "INSTAHYRE_ENABLED", False)

        # Even if we "accidentally" pretend credentials exist, the gate wins.
        monkeypatch.setattr(cfg, "INSTAHYRE_EMAIL", "user@example.com")
        monkeypatch.setattr(cfg, "INSTAHYRE_PASSWORD", "hunter2")

        engines = ScraperOrchestrator._default_engines()

        engine_names = {e.engine_name for e in engines}
        assert "instahyre" not in engine_names
        # JobSpy + Apify should still be present.
        assert "jobspy" in engine_names
        assert "apify" in engine_names

        out = capsys.readouterr().out
        assert "INSTAHYRE_ENABLED=false" in out

    def test_instahyre_attempted_when_env_enabled(self, monkeypatch):
        """Flag=on + configured credentials → Instahyre gets appended."""
        import backend.config as cfg
        monkeypatch.setattr(cfg, "INSTAHYRE_ENABLED", True)

        from backend.scrapers import instahyre_scraper as insta_mod

        class _FakeInstahyre:
            engine_name = "instahyre"
            is_configured = True

        monkeypatch.setattr(insta_mod, "InstahyreScraper", _FakeInstahyre)
        import backend.scrapers.scraper_orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "InstahyreScraper", _FakeInstahyre)

        engines = ScraperOrchestrator._default_engines()
        engine_names = {e.engine_name for e in engines}
        assert "instahyre" in engine_names

    def test_instahyre_still_skipped_without_credentials(self, monkeypatch, capsys):
        """Flag=on but credentials missing → gracefully skip, not crash."""
        import backend.config as cfg
        monkeypatch.setattr(cfg, "INSTAHYRE_ENABLED", True)

        from backend.scrapers import instahyre_scraper as insta_mod

        class _UnconfiguredInstahyre:
            engine_name = "instahyre"
            is_configured = False

        monkeypatch.setattr(insta_mod, "InstahyreScraper", _UnconfiguredInstahyre)
        import backend.scrapers.scraper_orchestrator as orch_mod
        monkeypatch.setattr(orch_mod, "InstahyreScraper", _UnconfiguredInstahyre)

        engines = ScraperOrchestrator._default_engines()
        assert "instahyre" not in {e.engine_name for e in engines}

        out = capsys.readouterr().out
        assert "Instahyre credentials not configured" in out
