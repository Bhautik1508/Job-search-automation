"""
JobSpy scraper — uses python-jobspy to scrape LinkedIn, Indeed, Google Jobs, Glassdoor.

Note: Naukri is excluded because it requires CAPTCHA resolution.
      Naukri jobs are scraped via the Apify engine instead.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from backend.scrapers.base_scraper import BaseScraper, RawJob


class JobSpyScraper(BaseScraper):
    """Primary scraper using the python-jobspy library."""

    engine_name = "jobspy"

    # Maps python-jobspy site names to our portal names
    # NOTE: Naukri excluded (CAPTCHA), Glassdoor excluded (fails for Indian locations).
    #       Both are handled by the Apify engine instead.
    SITE_MAP = {
        "indeed": "indeed",
        "linkedin": "linkedin",
        "google": "google",
    }

    def __init__(self, sites: list[str] | None = None):
        """
        Args:
            sites: Subset of portals to scrape.
                   Defaults to indeed, linkedin, google, glassdoor.
        """
        self.sites = sites or list(self.SITE_MAP.keys())

    def scrape(
        self,
        search_term: str,
        location: str,
        results_wanted: int = 30,
        hours_old: int = 72,
    ) -> list[RawJob]:
        """
        Scrape jobs via python-jobspy for the given term and location.
        """
        # Import here to allow the rest of the codebase to load
        # even when python-jobspy is not installed (e.g. in tests).
        from jobspy import scrape_jobs

        try:
            df: pd.DataFrame = scrape_jobs(
                site_name=self.sites,
                search_term=search_term,
                location=location,
                results_wanted=results_wanted,
                hours_old=hours_old,
                country_indeed="India",
            )
        except Exception as e:
            print(f"[jobspy] scrape_jobs failed for '{search_term}' in '{location}': {e}")
            return []

        if df is None or df.empty:
            return []

        return self._dataframe_to_raw_jobs(df)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _dataframe_to_raw_jobs(self, df: pd.DataFrame) -> list[RawJob]:
        """Convert the JobSpy DataFrame into a list of RawJob objects."""
        jobs: list[RawJob] = []
        for _, row in df.iterrows():
            try:
                job = self._row_to_raw_job(row)
                jobs.append(job)
            except Exception as e:
                print(f"[jobspy] Error converting row: {e}")
        return jobs

    def _row_to_raw_job(self, row: pd.Series) -> RawJob:
        """Convert a single DataFrame row to a RawJob."""

        def _safe(col: str, default=None):
            val = row.get(col, default)
            if pd.isna(val):
                return default
            return val

        # Determine portal name
        site = str(_safe("site", "unknown")).lower()
        portal = self.SITE_MAP.get(site, site)

        # Parse posting date
        date_posted = None
        raw_date = _safe("date_posted")
        if raw_date is not None:
            if isinstance(raw_date, datetime):
                date_posted = raw_date
            elif isinstance(raw_date, str):
                try:
                    date_posted = datetime.fromisoformat(raw_date)
                except ValueError:
                    pass

        return RawJob(
            title=str(_safe("title", "")).strip(),
            company=str(_safe("company", "")).strip(),
            location=str(_safe("location", "")).strip() or None,
            description=str(_safe("description", "")).strip() or None,
            job_url=str(_safe("job_url", "")).strip() or None,
            source_portal=portal,
            source_engine="jobspy",
            external_id=str(_safe("id", "")).strip() or None,
            salary_min=_safe("min_amount"),
            salary_max=_safe("max_amount"),
            salary_currency=str(_safe("currency", "")).strip() or None,
            experience_required=str(_safe("experience_range", "")).strip() or None,
            skills=str(_safe("skills", "")).strip() or None,
            job_type=str(_safe("job_type", "")).strip() or None,
            work_mode=str(_safe("work_from_home_type", "")).strip() or None,
            date_posted=date_posted,
        )
