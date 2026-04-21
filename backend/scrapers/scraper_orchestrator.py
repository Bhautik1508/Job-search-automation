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
    ALLOWED_LOCATION_KEYWORDS,
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

            # Step 2b: Location filter — drop jobs whose location is outside
            # the allowed set. Some actors ignore the location hint and return
            # global results, so we enforce it post-scrape.
            in_region_jobs = self._filter_allowed_locations(relevant_jobs)
            location_filtered_out = len(relevant_jobs) - len(in_region_jobs)
            if location_filtered_out:
                print(
                    f"🗺️  Filtered out {location_filtered_out} out-of-region jobs "
                    f"(kept {len(in_region_jobs)}; allowed={ALLOWED_LOCATION_KEYWORDS})"
                )

            # Step 3: Fuzzy-deduplicate in-memory
            unique_jobs = deduplicate_jobs(in_region_jobs)
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
                "after_title_filter": len(relevant_jobs),
                "title_filtered_out": filtered_out,
                "after_location_filter": len(in_region_jobs),
                "location_filtered_out": location_filtered_out,
                "unique_after_dedup": len(unique_jobs),
                "new_inserted": jobs_new,
                "duplicates_skipped": jobs_duplicate,
                "scan_id": scan.id,
                "per_engine_counts": dict(getattr(self, "_per_engine_counts", {})),
                "per_engine_errors": dict(getattr(self, "_per_engine_errors", {})),
                "rejected_title_sample": list(getattr(self, "_rejected_sample", [])),
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

        Also populates self._per_engine_counts (engine_name → count) for
        diagnostic reporting via the API.
        """
        self._per_engine_counts: dict[str, int] = {}
        self._per_engine_errors: dict[str, str] = {}

        if not self.engines:
            return []

        def _run_one(engine: BaseScraper) -> tuple[str, list[RawJob], str | None]:
            print(f"🔄 Running {engine.engine_name} scraper...")
            try:
                jobs = engine.scrape_all(
                    search_terms=self.search_terms,
                    locations=self.locations,
                    results_wanted=JOBSPY_RESULTS_PER_SITE,
                    hours_old=JOBSPY_HOURS_OLD,
                )
                print(f"   ↳ {engine.engine_name} returned {len(jobs)} jobs")
                return engine.engine_name, jobs, None
            except Exception as e:
                print(f"   ↳ {engine.engine_name} FAILED: {e}")
                return engine.engine_name, [], str(e)

        all_jobs: list[RawJob] = []

        def _collect(name: str, jobs: list[RawJob], err: str | None):
            self._per_engine_counts[name] = len(jobs)
            if err:
                self._per_engine_errors[name] = err
            all_jobs.extend(jobs)

        if not self.parallel or len(self.engines) == 1:
            for engine in self.engines:
                _collect(*_run_one(engine))
            return all_jobs

        with ThreadPoolExecutor(max_workers=len(self.engines)) as pool:
            futures = {pool.submit(_run_one, e): e for e in self.engines}
            for fut in as_completed(futures):
                _collect(*fut.result())
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

        Instahyre is only included when:
          1. INSTAHYRE_ENABLED is true (Phase 6.5 — defaults off in production
             because Playwright + Chromium won't fit on Render's free tier),
             AND
          2. credentials (INSTAHYRE_EMAIL / INSTAHYRE_PASSWORD) are set.
        """
        from backend.config import INSTAHYRE_ENABLED

        engines: list[BaseScraper] = [JobSpyScraper(), ApifyScraper()]

        if not INSTAHYRE_ENABLED:
            print("[orchestrator] INSTAHYRE_ENABLED=false — skipping Instahyre.")
            return engines

        instahyre = InstahyreScraper()
        if instahyre.is_configured:
            engines.append(instahyre)
        else:
            print("[orchestrator] Instahyre credentials not configured — skipping.")

        return engines

    def _filter_relevant_titles(self, jobs: list[RawJob]) -> list[RawJob]:
        """
        Filter jobs by title relevancy.

        A job is kept if:
          1. Its title contains at least one RELEVANT_TITLE_KEYWORDS, AND
          2. Its title does NOT contain any IRRELEVANT_TITLE_KEYWORDS.

        Tracks a small sample of rejected titles on self for diagnostics —
        lets the API surface "why did the filter eat everything?" without
        dumping every title into the response.
        """
        filtered = []
        rejected_sample: list[dict] = []

        def _record(job: RawJob, reason: str) -> None:
            if len(rejected_sample) >= 10:
                return
            sample = {
                "title": job.title,
                "company": job.company,
                "portal": job.source_portal,
                "reason": reason,
            }
            # When the title is empty, the actor is returning records we're
            # not mapping correctly — dump the raw item keys + a short preview
            # so we can see which field names the actor actually uses.
            if not job.title and isinstance(job.extra, dict):
                sample["raw_keys"] = sorted(list(job.extra.keys()))[:25]
                sample["raw_preview"] = {
                    k: (str(v)[:80] if v is not None else None)
                    for k, v in list(job.extra.items())[:8]
                }
            rejected_sample.append(sample)

        for job in jobs:
            title_lower = (job.title or "").lower()

            bad_match = next((b for b in IRRELEVANT_TITLE_KEYWORDS if b in title_lower), None)
            if bad_match:
                _record(job, f"blocklist:{bad_match}")
                continue

            if any(good in title_lower for good in RELEVANT_TITLE_KEYWORDS):
                filtered.append(job)
            else:
                _record(job, "no-allowlist-match")

        self._rejected_sample = rejected_sample
        return filtered

    @staticmethod
    def _filter_allowed_locations(jobs: list[RawJob]) -> list[RawJob]:
        """
        Keep only jobs whose location field contains one of ALLOWED_LOCATION_KEYWORDS.

        Strict mode: jobs with empty/unknown location are dropped too, because
        actors sometimes return out-of-region results without a location field.
        Set ALLOWED_LOCATION_KEYWORDS="" to disable the filter entirely.
        """
        if not ALLOWED_LOCATION_KEYWORDS:
            return list(jobs)

        return [
            job for job in jobs
            if any(k in (job.location or "").lower() for k in ALLOWED_LOCATION_KEYWORDS)
        ]

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
