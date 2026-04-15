"""
Abstract base class for all scrapers.

Every scraper engine (JobSpy, Apify, Instahyre) implements this interface
so the orchestrator can treat them uniformly.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawJob:
    """
    Normalised intermediate representation of a scraped job.
    All scrapers convert their portal-specific output into this format
    before it reaches the deduplication/storage layer.
    """

    title: str
    company: str
    location: str | None = None
    description: str | None = None
    job_url: str | None = None

    # Source info
    source_portal: str = ""      # naukri, linkedin, indeed, google, glassdoor
    source_engine: str = ""      # jobspy, apify, instahyre
    external_id: str | None = None

    # Job details
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    experience_required: str | None = None
    skills: str | None = None     # comma-separated
    job_type: str | None = None   # full-time, contract, etc.
    work_mode: str | None = None  # remote, hybrid, onsite

    # Timestamps
    date_posted: datetime | None = None

    # Extra data (portal-specific; stored but not modeled)
    extra: dict = field(default_factory=dict)


class BaseScraper(ABC):
    """Interface that every scraper engine must implement."""

    engine_name: str = "base"

    @abstractmethod
    def scrape(
        self,
        search_term: str,
        location: str,
        results_wanted: int = 30,
        hours_old: int = 72,
    ) -> list[RawJob]:
        """
        Run a single scrape for *one* search term + location combo.

        Returns a list of RawJob instances.
        """
        ...

    def scrape_all(
        self,
        search_terms: list[str],
        locations: list[str],
        results_wanted: int = 30,
        hours_old: int = 72,
    ) -> list[RawJob]:
        """
        Convenience: run scrape() for every (term × location) combination.
        Sub-classes can override this for parallel execution.
        """
        all_jobs: list[RawJob] = []
        for term in search_terms:
            for loc in locations:
                try:
                    jobs = self.scrape(term, loc, results_wanted, hours_old)
                    all_jobs.extend(jobs)
                except Exception as e:
                    print(f"[{self.engine_name}] Error scraping '{term}' in '{loc}': {e}")
        return all_jobs
