"""
Apify scraper — uses the apify-client SDK to run pre-built Actors
for LinkedIn, Naukri, Indeed, and Glassdoor.
"""

from __future__ import annotations

from datetime import datetime, timezone

from backend.scrapers.base_scraper import BaseScraper, RawJob
from backend.config import APIFY_API_TOKEN, APIFY_ACTORS


class ApifyScraper(BaseScraper):
    """Secondary scraper using Apify cloud actors."""

    engine_name = "apify"

    def __init__(self, api_token: str | None = None, actors: dict | None = None):
        """
        Args:
            api_token: Apify API token. Falls back to env var.
            actors: Mapping of portal -> actor_id. Falls back to config defaults.
        """
        self.api_token = api_token if api_token is not None else APIFY_API_TOKEN
        self.actors = actors if actors is not None else APIFY_ACTORS

    @property
    def is_configured(self) -> bool:
        """Check if Apify is ready to use (has an API token)."""
        return bool(self.api_token)

    def scrape(
        self,
        search_term: str,
        location: str,
        results_wanted: int = 30,
        hours_old: int = 72,
    ) -> list[RawJob]:
        """
        Run all configured Apify actors for the given term & location.
        """
        if not self.is_configured:
            print("[apify] No API token configured — skipping Apify scrape.")
            return []

        all_jobs: list[RawJob] = []
        for portal, actor_id in self.actors.items():
            try:
                jobs = self._run_actor(actor_id, portal, search_term, location, results_wanted)
                all_jobs.extend(jobs)
            except Exception as e:
                print(f"[apify] Error running actor '{actor_id}' for {portal}: {e}")

        return all_jobs

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _run_actor(
        self,
        actor_id: str,
        portal: str,
        search_term: str,
        location: str,
        max_items: int,
    ) -> list[RawJob]:
        """Run a single Apify actor and return normalised RawJob list."""
        from apify_client import ApifyClient

        client = ApifyClient(self.api_token)

        # Build input — most job-scraping actors accept these common fields
        run_input = {
            "keywords": search_term,
            "location": location,
            "maxItems": max_items,
        }

        # Portal-specific adjustments
        if portal == "linkedin":
            run_input["searchUrl"] = (
                f"https://www.linkedin.com/jobs/search/?keywords={search_term}"
                f"&location={location}"
            )
        elif portal == "naukri":
            run_input["keyword"] = search_term
            run_input["location"] = location
        elif portal == "indeed":
            run_input["query"] = search_term
            run_input["location"] = location
            run_input["country"] = "IN"

        # Run the actor synchronously (waits for completion)
        run = client.actor(actor_id).call(run_input=run_input, timeout_secs=120)

        # Fetch results from the default dataset
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

        return [self._item_to_raw_job(item, portal) for item in items]

    def _item_to_raw_job(self, item: dict, portal: str) -> RawJob:
        """Convert an Apify dataset item to a RawJob."""

        # Different actors return different field names — normalise them
        title = (
            item.get("title")
            or item.get("jobTitle")
            or item.get("positionName")
            or ""
        )
        company = (
            item.get("company")
            or item.get("companyName")
            or item.get("employer")
            or ""
        )
        location = (
            item.get("location")
            or item.get("jobLocation")
            or item.get("place")
            or None
        )
        description = (
            item.get("description")
            or item.get("jobDescription")
            or item.get("text")
            or None
        )
        job_url = (
            item.get("url")
            or item.get("jobUrl")
            or item.get("link")
            or item.get("applyUrl")
            or None
        )
        salary = item.get("salary") or item.get("salaryRange") or None

        # Parse salary range if present
        salary_min = salary_max = None
        if isinstance(salary, dict):
            salary_min = salary.get("min")
            salary_max = salary.get("max")
        elif isinstance(salary, str) and "-" in salary:
            parts = salary.replace(",", "").split("-")
            try:
                salary_min = float(parts[0].strip().replace("₹", "").replace("$", ""))
                salary_max = float(parts[1].strip().replace("₹", "").replace("$", ""))
            except (ValueError, IndexError):
                pass

        # Parse date
        date_posted = None
        raw_date = item.get("postedAt") or item.get("datePosted") or item.get("publishedAt")
        if raw_date:
            if isinstance(raw_date, datetime):
                date_posted = raw_date
            elif isinstance(raw_date, str):
                try:
                    date_posted = datetime.fromisoformat(raw_date.replace("Z", "+00:00"))
                except ValueError:
                    pass

        return RawJob(
            title=str(title).strip(),
            company=str(company).strip(),
            location=str(location).strip() if location else None,
            description=str(description).strip() if description else None,
            job_url=str(job_url).strip() if job_url else None,
            source_portal=portal,
            source_engine="apify",
            external_id=item.get("id") or item.get("jobId") or None,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency=item.get("currency") or "INR",
            experience_required=item.get("experience") or item.get("experienceRange") or None,
            skills=item.get("skills") or None,
            job_type=item.get("jobType") or item.get("employmentType") or None,
            work_mode=item.get("workMode") or item.get("workType") or None,
            date_posted=date_posted,
            extra=item,  # Keep original payload for debugging
        )
