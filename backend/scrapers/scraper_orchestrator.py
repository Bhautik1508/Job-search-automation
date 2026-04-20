"""
Scraper Orchestrator — coordinates all scraping engines, deduplicates results,
and stores them in the database.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from backend.scrapers.base_scraper import BaseScraper, RawJob
from backend.scrapers.jobspy_scraper import JobSpyScraper
from backend.scrapers.apify_scraper import ApifyScraper
from backend.scrapers.instahyre_scraper import InstahyreScraper
from backend.utils.deduplicator import compute_dedup_hash, deduplicate_jobs
from backend.database.models import Job, init_db, get_engine, get_session_factory
from backend.database.crud import create_scrape_scan, complete_scrape_scan, bulk_insert_jobs
from backend.config import (
    SEARCH_VARIANTS, TARGET_CITIES, JOBSPY_RESULTS_PER_SITE, JOBSPY_HOURS_OLD,
    RELEVANT_TITLE_KEYWORDS, IRRELEVANT_TITLE_KEYWORDS,
)


class ScraperOrchestrator:
    """
    Runs all configured scraper engines in sequence, deduplicates results,
    and persists new jobs to the database.
    """

    def __init__(
        self,
        engines: list[BaseScraper] | None = None,
        search_terms: list[str] | None = None,
        locations: list[str] | None = None,
        db_url: str | None = None,
        parallel: bool = True,
    ):
        self.engines = engines or self._default_engines()
        self.search_terms = search_terms or SEARCH_VARIANTS
        self.locations = locations or TARGET_CITIES
        self.db_url = db_url
        self.parallel = parallel

        # Init DB
        self._engine = get_engine(self.db_url)
        init_db(self._engine)
        self._Session = get_session_factory(self._engine)

    def run(self) -> dict:
        """
        Execute a full scrape cycle:
          1. Scrape from all engines
          2. Deduplicate in-memory
          3. Store new jobs to DB
          4. Record the scan metadata

        Returns a summary dict.
        """
        session = self._Session()

        # Create scrape scan record
        scan = create_scrape_scan(
            session,
            engine="orchestrator",
            portals=",".join(self._get_portal_names()),
            search_term=", ".join(self.search_terms),
            location=", ".join(self.locations),
        )

        try:
            # Step 1: Scrape from all engines
            raw_jobs = self._scrape_all_engines()
            print(f"\n📦 Total raw jobs collected: {len(raw_jobs)}")

            # Step 2: Title relevancy filter FIRST — shrinks the set before the
            # quadratic fuzzy-dedup step runs.
            relevant_jobs = self._filter_relevant_titles(raw_jobs)
            filtered_out = len(raw_jobs) - len(relevant_jobs)
            if filtered_out:
                print(f"🚫 Filtered out {filtered_out} irrelevant titles (kept {len(relevant_jobs)})")

            # Step 3: Fuzzy-deduplicate in-memory
            unique_jobs = deduplicate_jobs(relevant_jobs)
            print(f"🔍 After fuzzy dedup: {len(unique_jobs)} unique jobs")

            # Step 4: Assign dedup hashes and convert to DB models
            db_jobs = [self._raw_to_db_job(j) for j in unique_jobs]

            # Step 5: Insert into DB (skipping existing hashes)
            jobs_new = bulk_insert_jobs(session, db_jobs)
            jobs_duplicate = len(db_jobs) - jobs_new
            print(f"✅ New jobs inserted: {jobs_new}")
            print(f"♻️  Duplicates skipped: {jobs_duplicate}")

            # Step 6: Update scan record
            complete_scrape_scan(
                session,
                scan,
                jobs_found=len(raw_jobs),
                jobs_new=jobs_new,
                jobs_duplicate=jobs_duplicate,
                status="completed",
            )

            return {
                "status": "completed",
                "total_raw": len(raw_jobs),
                "unique_after_dedup": len(unique_jobs),
                "new_inserted": jobs_new,
                "duplicates_skipped": jobs_duplicate,
                "scan_id": scan.id,
            }

        except Exception as e:
            complete_scrape_scan(
                session, scan,
                jobs_found=0, jobs_new=0, jobs_duplicate=0,
                status="failed", error_message=str(e),
            )
            raise
        finally:
            session.close()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scrape_all_engines(self) -> list[RawJob]:
        """
        Run all engines and collect results.

        Engines are independent (different upstream services), so we run
        them concurrently in a thread pool when `parallel=True`.
        """
        if not self.engines:
            return []

        def _run_one(engine: BaseScraper) -> list[RawJob]:
            print(f"🔄 Running {engine.engine_name} scraper...")
            try:
                jobs = engine.scrape_all(
                    search_terms=self.search_terms,
                    locations=self.locations,
                    results_wanted=JOBSPY_RESULTS_PER_SITE,
                    hours_old=JOBSPY_HOURS_OLD,
                )
                print(f"   ↳ {engine.engine_name} returned {len(jobs)} jobs")
                return jobs
            except Exception as e:
                print(f"   ↳ {engine.engine_name} FAILED: {e}")
                return []

        if not self.parallel or len(self.engines) == 1:
            all_jobs: list[RawJob] = []
            for engine in self.engines:
                all_jobs.extend(_run_one(engine))
            return all_jobs

        all_jobs = []
        with ThreadPoolExecutor(max_workers=len(self.engines)) as pool:
            futures = {pool.submit(_run_one, e): e for e in self.engines}
            for fut in as_completed(futures):
                all_jobs.extend(fut.result())
        return all_jobs

    def _get_portal_names(self) -> list[str]:
        """Collect all portal names across engines."""
        portals = set()
        for engine in self.engines:
            if hasattr(engine, "SITE_MAP"):
                portals.update(engine.SITE_MAP.values())
            elif hasattr(engine, "actors"):
                portals.update(engine.actors.keys())
            elif engine.engine_name == "instahyre":
                portals.add("instahyre")
        return sorted(portals)

    @staticmethod
    def _default_engines() -> list[BaseScraper]:
        """
        Build the default set of scraper engines.

        Instahyre is only included if credentials are configured,
        to avoid login failures cluttering the logs.
        """
        engines: list[BaseScraper] = [JobSpyScraper(), ApifyScraper()]

        instahyre = InstahyreScraper()
        if instahyre.is_configured:
            engines.append(instahyre)
        else:
            print("[orchestrator] Instahyre credentials not configured — skipping.")

        return engines

    @staticmethod
    def _filter_relevant_titles(jobs: list[RawJob]) -> list[RawJob]:
        """
        Filter jobs by title relevancy.

        A job is kept if:
          1. Its title contains at least one RELEVANT_TITLE_KEYWORDS, AND
          2. Its title does NOT contain any IRRELEVANT_TITLE_KEYWORDS.
        """
        filtered = []
        for job in jobs:
            title_lower = (job.title or "").lower()

            # Check blocklist first (fast rejection)
            if any(bad in title_lower for bad in IRRELEVANT_TITLE_KEYWORDS):
                continue

            # Check allowlist
            if any(good in title_lower for good in RELEVANT_TITLE_KEYWORDS):
                filtered.append(job)

        return filtered

    @staticmethod
    def _raw_to_db_job(raw: RawJob) -> Job:
        """Convert a RawJob to a SQLAlchemy Job model instance."""
        dedup_hash = compute_dedup_hash(raw)
        return Job(
            title=raw.title,
            company=raw.company,
            location=raw.location,
            description=raw.description,
            job_url=raw.job_url,
            source_portal=raw.source_portal,
            source_engine=raw.source_engine,
            external_id=raw.external_id,
            salary_min=raw.salary_min,
            salary_max=raw.salary_max,
            salary_currency=raw.salary_currency,
            experience_required=raw.experience_required,
            skills=raw.skills,
            job_type=raw.job_type,
            work_mode=raw.work_mode,
            date_posted=raw.date_posted,
            date_scraped=datetime.now(timezone.utc),
            dedup_hash=dedup_hash,
        )
