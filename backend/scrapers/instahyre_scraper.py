"""
Instahyre scraper — custom Playwright-based scraper for instahyre.com.

Instahyre is a JS-heavy invite-only job portal popular in the Indian market.
It requires:
  1. Login (email + password)
  2. Navigate to the recommendation / search page
  3. Extract job cards from the dynamically-rendered DOM

Playwright is used instead of Selenium for superior reliability with
single-page applications and built-in auto-waiting.
"""

from __future__ import annotations

import re
import time
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from backend.scrapers.base_scraper import BaseScraper, RawJob
from backend.config import (
    INSTAHYRE_EMAIL,
    INSTAHYRE_PASSWORD,
    INSTAHYRE_HEADLESS,
    INSTAHYRE_TIMEOUT_MS,
    INSTAHYRE_MAX_PAGES,
)

logger = logging.getLogger(__name__)


class InstahyreScraper(BaseScraper):
    """
    Custom Playwright scraper for Instahyre.

    Since Instahyre is invite-only and JS-rendered, we use a real
    browser session via Playwright to:
      - Log in with email/password
      - Navigate to job recommendations or search results
      - Paginate through results
      - Extract structured job data from the DOM
    """

    engine_name = "instahyre"

    # URLs
    BASE_URL = "https://www.instahyre.com"
    LOGIN_URL = "https://www.instahyre.com/login/"
    JOBS_URL = "https://www.instahyre.com/candidate/opportunities/"

    def __init__(
        self,
        email: str | None = None,
        password: str | None = None,
        headless: bool | None = None,
        timeout_ms: int | None = None,
        max_pages: int | None = None,
    ):
        self.email = email or INSTAHYRE_EMAIL
        self.password = password or INSTAHYRE_PASSWORD
        self.headless = headless if headless is not None else INSTAHYRE_HEADLESS
        self.timeout_ms = timeout_ms if timeout_ms is not None else INSTAHYRE_TIMEOUT_MS
        self.max_pages = max_pages if max_pages is not None else INSTAHYRE_MAX_PAGES

    @property
    def is_configured(self) -> bool:
        """Check if Instahyre credentials are available."""
        return bool(self.email) and bool(self.password)

    def scrape(
        self,
        search_term: str,
        location: str,
        results_wanted: int = 30,
        hours_old: int = 72,
    ) -> list[RawJob]:
        """
        Scrape Instahyre for jobs matching the given criteria.

        Since Instahyre uses recommendation-based listing (not free-text search),
        we navigate to the opportunities page and extract all visible jobs,
        then filter by search_term and location client-side.
        """
        if not self.is_configured:
            logger.warning("[instahyre] No credentials configured — skipping.")
            return []

        try:
            return self._run_browser_scrape(search_term, location, results_wanted)
        except Exception as e:
            logger.error(f"[instahyre] Browser scrape failed: {e}")
            return []

    def _run_browser_scrape(
        self,
        search_term: str,
        location: str,
        max_results: int,
    ) -> list[RawJob]:
        """
        Launch a Playwright browser, log in, and extract job listings.
        """
        # Lazy import — allows the rest of the codebase to work
        # even when Playwright is not installed
        from playwright.sync_api import sync_playwright

        jobs: list[RawJob] = []

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless)
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
            )
            page = context.new_page()
            page.set_default_timeout(self.timeout_ms)

            try:
                # Step 1: Login
                if not self._login(page):
                    logger.error("[instahyre] Login failed — aborting scrape.")
                    return []

                # Step 2: Navigate to opportunities page
                page.goto(self.JOBS_URL, wait_until="networkidle")
                time.sleep(2)  # Let dynamic content settle

                # Step 3: Extract jobs across pages
                page_num = 0
                while page_num < self.max_pages and len(jobs) < max_results:
                    page_jobs = self._extract_jobs_from_page(page)
                    if not page_jobs:
                        break

                    jobs.extend(page_jobs)
                    logger.info(
                        f"[instahyre] Page {page_num + 1}: extracted {len(page_jobs)} jobs "
                        f"(total: {len(jobs)})"
                    )

                    # Try to go to next page
                    if not self._go_to_next_page(page):
                        break
                    page_num += 1

                # Step 4: Filter by search term and location
                jobs = self._filter_jobs(jobs, search_term, location)

            except Exception as e:
                logger.error(f"[instahyre] Scrape error: {e}")
            finally:
                context.close()
                browser.close()

        return jobs[:max_results]

    def _login(self, page) -> bool:
        """
        Perform the Instahyre login flow.

        Returns True if login succeeds, False otherwise.
        """
        try:
            page.goto(self.LOGIN_URL, wait_until="networkidle")
            time.sleep(1)

            # Fill in the email field
            email_input = page.locator('input[type="email"], input[name="email"], #email')
            if email_input.count() == 0:
                # Try broader selector
                email_input = page.locator('input[placeholder*="email" i]')

            if email_input.count() > 0:
                email_input.first.fill(self.email)
            else:
                logger.error("[instahyre] Could not find email input field")
                return False

            # Fill in the password field
            password_input = page.locator('input[type="password"]')
            if password_input.count() > 0:
                password_input.first.fill(self.password)
            else:
                logger.error("[instahyre] Could not find password input field")
                return False

            # Click login button
            login_btn = page.locator(
                'button[type="submit"], '
                'button:has-text("Login"), '
                'button:has-text("Sign in"), '
                'input[type="submit"]'
            )
            if login_btn.count() > 0:
                login_btn.first.click()
            else:
                logger.error("[instahyre] Could not find login button")
                return False

            # Wait for navigation after login
            page.wait_for_load_state("networkidle")
            time.sleep(2)

            # Verify login succeeded by checking URL or page content
            if "login" in page.url.lower() and "candidate" not in page.url.lower():
                # Still on login page — probably failed
                error_el = page.locator('.error, .alert-danger, [class*="error"]')
                if error_el.count() > 0:
                    logger.error(
                        f"[instahyre] Login error: {error_el.first.text_content()}"
                    )
                return False

            logger.info("[instahyre] Login successful")
            return True

        except Exception as e:
            logger.error(f"[instahyre] Login exception: {e}")
            return False

    def _extract_jobs_from_page(self, page) -> list[RawJob]:
        """
        Extract all job cards from the current page.

        Instahyre job cards typically contain:
          - Job title
          - Company name
          - Location
          - Experience range
          - Skills/tags
          - Job URL
        """
        jobs: list[RawJob] = []

        # Instahyre uses various card selectors depending on the page version
        card_selectors = [
            ".opportunity-card",
            ".job-card",
            '[class*="opportunity"]',
            '[class*="job-listing"]',
            ".card.opportunity",
            'div[data-opportunity-id]',
        ]

        cards = None
        for selector in card_selectors:
            cards = page.locator(selector)
            if cards.count() > 0:
                break

        if not cards or cards.count() == 0:
            logger.warning("[instahyre] No job cards found on page")
            return []

        for i in range(cards.count()):
            try:
                card = cards.nth(i)
                job = self._parse_card(card, page)
                if job:
                    jobs.append(job)
            except Exception as e:
                logger.debug(f"[instahyre] Error parsing card {i}: {e}")

        return jobs

    def _parse_card(self, card, page) -> Optional[RawJob]:
        """Parse a single job card element into a RawJob."""

        # Title
        title = self._safe_text(card, [
            "h3", "h4",
            '[class*="title"]',
            '[class*="designation"]',
            ".opportunity-title",
        ])
        if not title:
            return None

        # Company
        company = self._safe_text(card, [
            '[class*="company"]',
            '[class*="employer"]',
            ".company-name",
            "h5",
        ])

        # Location
        location = self._safe_text(card, [
            '[class*="location"]',
            '[class*="place"]',
            '.location',
        ])

        # Experience
        experience = self._safe_text(card, [
            '[class*="experience"]',
            '[class*="exp"]',
        ])

        # Skills
        skills_elements = card.locator('[class*="skill"], .tag, .chip')
        skills_list = []
        for j in range(min(skills_elements.count(), 20)):
            skill_text = skills_elements.nth(j).text_content()
            if skill_text:
                skills_list.append(skill_text.strip())
        skills = ", ".join(skills_list) if skills_list else None

        # Job URL — try to find a link within the card
        link = card.locator("a[href]")
        job_url = None
        if link.count() > 0:
            href = link.first.get_attribute("href")
            if href:
                if href.startswith("/"):
                    job_url = f"{self.BASE_URL}{href}"
                elif href.startswith("http"):
                    job_url = href

        # Salary
        salary_text = self._safe_text(card, [
            '[class*="salary"]',
            '[class*="ctc"]',
            '[class*="compensation"]',
        ])
        salary_min, salary_max = self._parse_salary(salary_text)

        # Description (may not be visible on card)
        description = self._safe_text(card, [
            '[class*="description"]',
            '[class*="about"]',
        ])

        # External ID from data attributes
        external_id = (
            card.get_attribute("data-opportunity-id")
            or card.get_attribute("data-id")
            or card.get_attribute("id")
        )

        # Date posted
        date_text = self._safe_text(card, [
            '[class*="posted"]',
            '[class*="date"]',
            '[class*="time"]',
            "time",
        ])
        date_posted = self._parse_relative_date(date_text)

        return RawJob(
            title=title.strip(),
            company=(company or "").strip(),
            location=location.strip() if location else None,
            description=description.strip() if description else None,
            job_url=job_url,
            source_portal="instahyre",
            source_engine="instahyre",
            external_id=external_id,
            salary_min=salary_min,
            salary_max=salary_max,
            salary_currency="INR" if (salary_min or salary_max) else None,
            experience_required=experience.strip() if experience else None,
            skills=skills,
            date_posted=date_posted,
        )

    def _go_to_next_page(self, page) -> bool:
        """
        Attempt to navigate to the next page of results.

        Returns True if a new page was loaded, False if we're at the end.
        """
        try:
            next_selectors = [
                'a:has-text("Next")',
                'button:has-text("Next")',
                '[class*="next"]',
                'a[rel="next"]',
                '.pagination .next a',
                'button[aria-label="Next page"]',
            ]

            for selector in next_selectors:
                next_btn = page.locator(selector)
                if next_btn.count() > 0 and next_btn.first.is_visible():
                    next_btn.first.click()
                    page.wait_for_load_state("networkidle")
                    time.sleep(1.5)
                    return True

            # Try infinite scroll as fallback
            return self._try_infinite_scroll(page)

        except Exception as e:
            logger.debug(f"[instahyre] No next page: {e}")
            return False

    def _try_infinite_scroll(self, page) -> bool:
        """
        Some Instahyre pages use infinite scroll.
        Scroll down and check if new content loaded.
        """
        try:
            # Get current number of cards
            before_count = page.locator(
                '.opportunity-card, .job-card, [class*="opportunity"]'
            ).count()

            # Scroll to bottom
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            time.sleep(2)

            # Check if new cards appeared
            after_count = page.locator(
                '.opportunity-card, .job-card, [class*="opportunity"]'
            ).count()

            return after_count > before_count

        except Exception:
            return False

    # ------------------------------------------------------------------
    # Utility helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_text(element, selectors: list[str]) -> str | None:
        """
        Try multiple selectors on an element, return the first non-empty text.
        """
        for selector in selectors:
            try:
                el = element.locator(selector)
                if el.count() > 0:
                    text = el.first.text_content()
                    if text and text.strip():
                        return text.strip()
            except Exception:
                pass
        return None

    @staticmethod
    def _parse_salary(salary_text: str | None) -> tuple[float | None, float | None]:
        """
        Parse salary text like '₹12L - ₹18L' or '12-18 LPA' into (min, max).
        """
        if not salary_text:
            return None, None

        try:
            # Normalise: remove ₹, commas, spaces
            text = salary_text.replace("₹", "").replace(",", "").strip()

            # Look for patterns like "12L - 18L" or "12-18 LPA"
            match = re.search(
                r'(\d+(?:\.\d+)?)\s*(?:L|[Ll]akh|LPA)?\s*[-–to]+\s*(\d+(?:\.\d+)?)\s*(?:L|[Ll]akh|LPA)?',
                text,
            )
            if match:
                min_val = float(match.group(1))
                max_val = float(match.group(2))

                # If values are small, they're in Lakhs — convert to absolute
                if min_val < 1000:
                    min_val *= 100_000  # 1 Lakh = 100,000
                if max_val < 1000:
                    max_val *= 100_000

                return min_val, max_val

        except (ValueError, AttributeError):
            pass

        return None, None

    @staticmethod
    def _parse_relative_date(date_text: str | None) -> datetime | None:
        """
        Parse relative date strings like '2 days ago', '1 week ago', 'Just now'.
        """
        if not date_text:
            return None

        text = date_text.lower().strip()
        now = datetime.now(timezone.utc)

        try:
            if "just now" in text or "moment" in text:
                return now

            match = re.search(r'(\d+)\s*(minute|hour|day|week|month)s?\s*ago', text)
            if match:
                amount = int(match.group(1))
                unit = match.group(2)

                if unit == "minute":
                    return now - timedelta(minutes=amount)
                elif unit == "hour":
                    return now - timedelta(hours=amount)
                elif unit == "day":
                    return now - timedelta(days=amount)
                elif unit == "week":
                    return now - timedelta(weeks=amount)
                elif unit == "month":
                    return now - timedelta(days=amount * 30)

            # Try ISO format
            return datetime.fromisoformat(text.replace("Z", "+00:00"))

        except (ValueError, AttributeError):
            return None

    @staticmethod
    def _filter_jobs(
        jobs: list[RawJob],
        search_term: str,
        location: str,
    ) -> list[RawJob]:
        """
        Client-side filter — Instahyre shows all recommendations,
        so we filter by search term and location after extraction.
        """
        term_lower = search_term.lower()
        location_lower = location.lower()

        filtered = []
        for job in jobs:
            title_lower = (job.title or "").lower()
            loc_lower = (job.location or "").lower()
            desc_lower = (job.description or "").lower()

            # Check if the job title or description matches the search term
            term_match = (
                term_lower in title_lower
                or term_lower in desc_lower
                # Also check individual words for partial matching
                or all(word in title_lower for word in term_lower.split())
            )

            # Location matching (flexible — "Bangalore" matches "Bengaluru" etc.)
            location_aliases = {
                "bangalore": ["bangalore", "bengaluru", "blr"],
                "mumbai": ["mumbai", "bombay"],
                "delhi ncr": ["delhi", "ncr", "gurgaon", "gurugram", "noida", "new delhi"],
                "hyderabad": ["hyderabad", "hyd"],
                "pune": ["pune"],
                "chennai": ["chennai", "madras"],
                "kolkata": ["kolkata", "calcutta"],
            }

            loc_match = location_lower in loc_lower
            if not loc_match:
                for aliases in location_aliases.values():
                    if any(alias in location_lower for alias in aliases):
                        loc_match = any(alias in loc_lower for alias in aliases)
                        break

            # Also allow "remote" jobs
            if not loc_match and "remote" in loc_lower:
                loc_match = True

            if term_match and (loc_match or not location):
                filtered.append(job)

        return filtered
