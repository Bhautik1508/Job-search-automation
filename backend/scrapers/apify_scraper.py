"""
Apify scraper — uses the apify-client SDK to run pre-built Actors
for LinkedIn, Naukri, Indeed, and Glassdoor.

Phase 4.5 enhancements:
  - Banking-specific search queries for deeper fintech/banking coverage
  - Portal-specific actor input tuning (proxy, geo, pagination)
  - Credit usage monitoring with configurable threshold warnings
  - Extended timeout and max-items configuration
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from backend.scrapers.base_scraper import BaseScraper, RawJob
from backend.config import (
    APIFY_API_TOKEN,
    APIFY_ACTORS,
    APIFY_ACTOR_TIMEOUT,
    APIFY_MAX_ITEMS_PER_ACTOR,
    APIFY_ENABLE_BANKING_QUERIES,
    APIFY_CREDIT_WARNING_THRESHOLD,
    APIFY_BANKING_SEARCH_VARIANTS,
    APIFY_MAX_CITIES,
    APIFY_MAX_PORTALS,
    APIFY_PORTAL_PRIORITY,
)

logger = logging.getLogger(__name__)


class ApifyScraper(BaseScraper):
    """Secondary scraper using Apify cloud actors."""

    engine_name = "apify"

    def __init__(
        self,
        api_token: str | None = None,
        actors: dict | None = None,
        timeout_secs: int | None = None,
        max_items: int | None = None,
        enable_banking_queries: bool | None = None,
        credit_warning_threshold: float | None = None,
        max_cities: int | None = None,
        max_portals: int | None = None,
        portal_priority: list[str] | None = None,
    ):
        """
        Args:
            api_token: Apify API token. Falls back to env var.
            actors: Mapping of portal -> actor_id. Falls back to config defaults.
            timeout_secs: Actor run timeout in seconds.
            max_items: Maximum items to fetch per actor run.
            enable_banking_queries: Whether to also run banking-specific searches.
            credit_warning_threshold: Fraction (0-1) of credits used that triggers a warning.
            max_cities: Top N cities from the caller's list that actually get scraped.
            max_portals: Top N portals (by priority) that actually get run.
            portal_priority: Order in which portals are selected when pruning.
        """
        self.api_token = api_token if api_token is not None else APIFY_API_TOKEN
        self.actors = actors if actors is not None else APIFY_ACTORS
        self.timeout_secs = timeout_secs if timeout_secs is not None else APIFY_ACTOR_TIMEOUT
        self.max_items = max_items if max_items is not None else APIFY_MAX_ITEMS_PER_ACTOR
        self.enable_banking_queries = (
            enable_banking_queries
            if enable_banking_queries is not None
            else APIFY_ENABLE_BANKING_QUERIES
        )
        self.credit_warning_threshold = (
            credit_warning_threshold
            if credit_warning_threshold is not None
            else APIFY_CREDIT_WARNING_THRESHOLD
        )
        self.max_cities = max_cities if max_cities is not None else APIFY_MAX_CITIES
        self.max_portals = max_portals if max_portals is not None else APIFY_MAX_PORTALS
        self.portal_priority = (
            portal_priority if portal_priority is not None else APIFY_PORTAL_PRIORITY
        )

        # Prune the actor map to the top-priority portals.
        self.actors = self._prune_actors(self.actors)

        # Track credit usage across runs
        self._credit_usage_log: list[dict] = []

    def _prune_actors(self, actors: dict) -> dict:
        """Keep at most max_portals actors, selected by portal_priority order."""
        if self.max_portals <= 0 or len(actors) <= self.max_portals:
            return dict(actors)
        ordered = [p for p in self.portal_priority if p in actors]
        # Append any portals not in the priority list to keep them as fallback order.
        ordered += [p for p in actors if p not in ordered]
        return {p: actors[p] for p in ordered[: self.max_portals]}

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
            logger.info("[apify] No API token configured — skipping Apify scrape.")
            return []

        all_jobs: list[RawJob] = []
        for portal, actor_id in self.actors.items():
            try:
                jobs = self._run_actor(actor_id, portal, search_term, location, results_wanted)
                all_jobs.extend(jobs)
            except Exception as e:
                logger.error(f"[apify] Error running actor '{actor_id}' for {portal}: {e}")

        return all_jobs

    def scrape_all(
        self,
        search_terms: list[str],
        locations: list[str],
        results_wanted: int = 30,
        hours_old: int = 72,
    ) -> list[RawJob]:
        """
        Override scrape_all to inject banking-specific queries when enabled.

        Phase 4.5: In addition to the standard search terms, this also
        runs banking-specific search variants for deeper fintech/banking coverage.
        """
        all_jobs: list[RawJob] = []

        # Phase 5: Cap cities to conserve Apify credits. Callers pass locations
        # in priority order; we keep the first N.
        effective_locations = (
            locations[: self.max_cities] if self.max_cities > 0 else list(locations)
        )
        if len(effective_locations) < len(locations):
            logger.info(
                f"[apify] Pruned locations: {len(locations)} → {len(effective_locations)} "
                f"(APIFY_MAX_CITIES={self.max_cities})"
            )

        # Run base search terms
        for term in search_terms:
            for loc in effective_locations:
                try:
                    jobs = self.scrape(term, loc, results_wanted, hours_old)
                    all_jobs.extend(jobs)
                except Exception as e:
                    logger.error(f"[apify] Error scraping '{term}' in '{loc}': {e}")

        # Phase 4.5: Run banking-specific queries for deeper coverage
        if self.enable_banking_queries:
            banking_terms = APIFY_BANKING_SEARCH_VARIANTS
            logger.info(
                f"[apify] Running {len(banking_terms)} banking-specific queries "
                f"across {len(effective_locations)} locations"
            )
            for term in banking_terms:
                for loc in effective_locations:
                    try:
                        jobs = self.scrape(term, loc, results_wanted, hours_old)
                        all_jobs.extend(jobs)
                    except Exception as e:
                        logger.error(f"[apify] Banking query error '{term}' in '{loc}': {e}")

        # Check credit usage after all runs
        self._check_credit_usage()

        return all_jobs

    # ------------------------------------------------------------------
    # Credit usage monitoring (Phase 4.5)
    # ------------------------------------------------------------------

    def check_credit_balance(self) -> dict | None:
        """
        Query the Apify API for current credit usage.

        Returns a dict with:
          - total_credits: total credits on the plan
          - used_credits: credits used this billing period
          - remaining_credits: credits remaining
          - usage_fraction: fraction of credits used (0-1)
          - warning: True if usage exceeds the threshold
        """
        if not self.is_configured:
            return None

        try:
            from apify_client import ApifyClient
            client = ApifyClient(self.api_token)

            # Fetch user info (includes billing/usage data)
            user_info = client.user().get()
            if not user_info:
                return None

            # Extract plan and usage data
            plan = user_info.get("plan", {})
            usage = user_info.get("usage", {})

            total = plan.get("monthlyUsageCreditsUsd", 5.0)
            used = usage.get("monthlyUsageCreditsUsd", 0.0)
            remaining = max(0.0, total - used)
            fraction = used / total if total > 0 else 0.0

            result = {
                "total_credits": total,
                "used_credits": round(used, 4),
                "remaining_credits": round(remaining, 4),
                "usage_fraction": round(fraction, 4),
                "warning": fraction >= self.credit_warning_threshold,
            }

            self._credit_usage_log.append(result)
            return result

        except Exception as e:
            logger.warning(f"[apify] Failed to check credit balance: {e}")
            return None

    def get_credit_usage_log(self) -> list[dict]:
        """Return the history of credit usage checks."""
        return list(self._credit_usage_log)

    def _check_credit_usage(self):
        """Log a warning if credit usage exceeds the threshold."""
        balance = self.check_credit_balance()
        if balance and balance["warning"]:
            logger.warning(
                f"[apify] ⚠️ Credit usage at {balance['usage_fraction']:.0%} "
                f"(${balance['used_credits']:.2f} / ${balance['total_credits']:.2f}). "
                f"Consider reducing actor runs or upgrading plan."
            )
        elif balance:
            logger.info(
                f"[apify] 💰 Credit usage: ${balance['used_credits']:.2f} / "
                f"${balance['total_credits']:.2f} "
                f"({balance['usage_fraction']:.0%})"
            )

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

        # Phase 4.5: Build optimised, portal-specific input
        run_input = self._build_actor_input(portal, search_term, location, max_items)

        # Run the actor synchronously (waits for completion)
        run = client.actor(actor_id).call(
            run_input=run_input,
            timeout_secs=self.timeout_secs,
        )

        # Fetch results from the default dataset
        items = list(client.dataset(run["defaultDatasetId"]).iterate_items())

        return [self._item_to_raw_job(item, portal) for item in items]

    def _build_actor_input(
        self,
        portal: str,
        search_term: str,
        location: str,
        max_items: int,
    ) -> dict:
        """
        Phase 4.5: Build portal-specific, optimised actor input.

        Each Apify actor has a different input schema. This method tailors
        the input for best results per portal.
        """
        effective_max = min(max_items, self.max_items)

        if portal == "linkedin":
            return {
                "searchUrl": (
                    f"https://www.linkedin.com/jobs/search/?keywords={search_term}"
                    f"&location={location}"
                    f"&f_TPR=r86400"         # Last 24 hours (fresher results)
                    f"&f_WT=2"               # Remote jobs included
                ),
                "maxItems": effective_max,
                "proxy": {
                    "useApifyProxy": True,
                    "apifyProxyGroups": ["RESIDENTIAL"],
                },
                "startPage": 0,
                "maxPages": 3,  # Limit pages to conserve credits
            }

        elif portal == "naukri":
            return {
                "keyword": search_term,
                "location": location,
                "maxItems": effective_max,
                "experience": "2-8",         # PM-level experience range
                "sortBy": "date",            # Freshest first
                "freshness": 3,              # Last 3 days
                "jobType": "fulltime",       # Filter to full-time roles
            }

        elif portal == "indeed":
            return {
                "query": search_term,
                "location": location,
                "country": "IN",
                "maxItems": effective_max,
                "sort": "date",              # Sort by date for freshness
                "fromage": 3,                # Posted in last 3 days
                "jobType": "fulltime",
            }

        elif portal == "glassdoor":
            return {
                "keyword": search_term,
                "location": location,
                "maxItems": effective_max,
                "sortBy": "date_desc",
                "fromAge": 3,                # Last 3 days
                "jobType": "fulltime",
                "country": "India",
            }

        else:
            # Fallback: generic input that most actors accept
            return {
                "keywords": search_term,
                "location": location,
                "maxItems": effective_max,
            }

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
