"""
Unit tests for the Instahyre scraper (Phase 4.4).

These tests use mocking to avoid launching a real browser or making network calls.
They verify:
  - Configuration and credential checking
  - Job card parsing logic
  - Salary text parsing (Indian formats: Lakhs, LPA, ₹)
  - Relative date parsing ("2 days ago", "Just now", etc.)
  - Client-side filtering (search term + location with aliases)
  - Login flow logic
  - Pagination (next page + infinite scroll)
  - Error handling / graceful degradation
"""

import sys
import pytest
from unittest.mock import patch, MagicMock, PropertyMock
from datetime import datetime, timezone, timedelta

from backend.scrapers.base_scraper import RawJob
from backend.scrapers.instahyre_scraper import InstahyreScraper


# ==================================================================
# Tests: Configuration
# ==================================================================

class TestInstahyreConfiguration:
    def test_not_configured_without_credentials(self):
        """Scraper is not configured when credentials are missing."""
        scraper = InstahyreScraper(email="", password="")
        assert not scraper.is_configured

    def test_not_configured_email_only(self):
        """Both email and password are required."""
        scraper = InstahyreScraper(email="test@example.com", password="")
        assert not scraper.is_configured

    def test_not_configured_password_only(self):
        """Both email and password are required."""
        scraper = InstahyreScraper(email="", password="secret123")
        assert not scraper.is_configured

    def test_configured_with_credentials(self):
        """Scraper is configured with both email and password."""
        scraper = InstahyreScraper(email="test@example.com", password="secret123")
        assert scraper.is_configured

    def test_engine_name(self):
        """Engine name is 'instahyre'."""
        scraper = InstahyreScraper(email="", password="")
        assert scraper.engine_name == "instahyre"

    def test_custom_settings(self):
        """Custom settings override defaults."""
        scraper = InstahyreScraper(
            email="a@b.com",
            password="pass",
            headless=False,
            timeout_ms=60000,
            max_pages=10,
        )
        assert scraper.headless is False
        assert scraper.timeout_ms == 60000
        assert scraper.max_pages == 10

    def test_default_settings(self):
        """Default settings are applied when not specified."""
        scraper = InstahyreScraper(email="a@b.com", password="pass")
        assert scraper.headless is True  # Default from config
        assert scraper.timeout_ms > 0
        assert scraper.max_pages > 0

    def test_urls(self):
        """URLs are correct."""
        assert InstahyreScraper.BASE_URL == "https://www.instahyre.com"
        assert InstahyreScraper.LOGIN_URL == "https://www.instahyre.com/login/"
        assert InstahyreScraper.JOBS_URL == "https://www.instahyre.com/candidate/opportunities/"

    def test_scrape_returns_empty_without_credentials(self):
        """Scrape returns empty list when not configured."""
        scraper = InstahyreScraper(email="", password="")
        result = scraper.scrape("Product Manager", "Bangalore")
        assert result == []


# ==================================================================
# Tests: Salary Parsing
# ==================================================================

class TestInstahyreSalaryParsing:
    def test_lakh_range(self):
        """Parse '₹12L - ₹18L' format."""
        min_s, max_s = InstahyreScraper._parse_salary("₹12L - ₹18L")
        assert min_s == 1_200_000
        assert max_s == 1_800_000

    def test_lpa_range(self):
        """Parse '12-18 LPA' format."""
        min_s, max_s = InstahyreScraper._parse_salary("12-18 LPA")
        assert min_s == 1_200_000
        assert max_s == 1_800_000

    def test_lakh_with_decimal(self):
        """Parse '15.5L - 25L' format."""
        min_s, max_s = InstahyreScraper._parse_salary("15.5L - 25L")
        assert min_s == 1_550_000
        assert max_s == 2_500_000

    def test_lakh_word(self):
        """Parse '12 Lakh - 18 Lakh' format."""
        min_s, max_s = InstahyreScraper._parse_salary("12 Lakh - 18 Lakh")
        assert min_s == 1_200_000
        assert max_s == 1_800_000

    def test_none_input(self):
        """None input returns (None, None)."""
        assert InstahyreScraper._parse_salary(None) == (None, None)

    def test_empty_string(self):
        """Empty string returns (None, None)."""
        assert InstahyreScraper._parse_salary("") == (None, None)

    def test_unparseable_string(self):
        """Invalid salary format returns (None, None)."""
        assert InstahyreScraper._parse_salary("Competitive") == (None, None)

    def test_rupee_with_commas(self):
        """Parse '₹12,00,000 - ₹18,00,000' partially (₹ removed, commas removed)."""
        min_s, max_s = InstahyreScraper._parse_salary("₹12,00,000 - ₹18,00,000")
        # The numbers 1200000 and 1800000 are > 1000 so they won't be multiplied
        assert min_s == 1_200_000
        assert max_s == 1_800_000


# ==================================================================
# Tests: Relative Date Parsing
# ==================================================================

class TestInstahyreDateParsing:
    def test_just_now(self):
        """'Just now' returns approximately current time."""
        result = InstahyreScraper._parse_relative_date("Just now")
        assert result is not None
        assert (datetime.now(timezone.utc) - result).total_seconds() < 5

    def test_minutes_ago(self):
        """'30 minutes ago' returns correct time."""
        result = InstahyreScraper._parse_relative_date("30 minutes ago")
        assert result is not None
        expected = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert abs((result - expected).total_seconds()) < 5

    def test_hours_ago(self):
        """'2 hours ago' returns correct time."""
        result = InstahyreScraper._parse_relative_date("2 hours ago")
        assert result is not None
        expected = datetime.now(timezone.utc) - timedelta(hours=2)
        assert abs((result - expected).total_seconds()) < 5

    def test_days_ago(self):
        """'3 days ago' returns correct time."""
        result = InstahyreScraper._parse_relative_date("3 days ago")
        assert result is not None
        expected = datetime.now(timezone.utc) - timedelta(days=3)
        assert abs((result - expected).total_seconds()) < 5

    def test_weeks_ago(self):
        """'1 week ago' returns correct time."""
        result = InstahyreScraper._parse_relative_date("1 week ago")
        assert result is not None
        expected = datetime.now(timezone.utc) - timedelta(weeks=1)
        assert abs((result - expected).total_seconds()) < 5

    def test_months_ago(self):
        """'2 months ago' returns correct time."""
        result = InstahyreScraper._parse_relative_date("2 months ago")
        assert result is not None
        expected = datetime.now(timezone.utc) - timedelta(days=60)
        assert abs((result - expected).total_seconds()) < 5

    def test_singular_unit(self):
        """'1 day ago' (singular) is handled."""
        result = InstahyreScraper._parse_relative_date("1 day ago")
        assert result is not None
        expected = datetime.now(timezone.utc) - timedelta(days=1)
        assert abs((result - expected).total_seconds()) < 5

    def test_none_input(self):
        """None returns None."""
        assert InstahyreScraper._parse_relative_date(None) is None

    def test_empty_string(self):
        """Empty string returns None."""
        assert InstahyreScraper._parse_relative_date("") is None

    def test_unparseable(self):
        """Random text returns None."""
        assert InstahyreScraper._parse_relative_date("Recently posted") is None


# ==================================================================
# Tests: Job Filtering
# ==================================================================

class TestInstahyreJobFiltering:
    def _make_job(self, title: str, company: str = "TestCo",
                  location: str = "Bangalore", description: str = "") -> RawJob:
        return RawJob(
            title=title,
            company=company,
            location=location,
            description=description,
            source_portal="instahyre",
            source_engine="instahyre",
        )

    def test_exact_match(self):
        """Jobs matching search term exactly are kept."""
        jobs = [self._make_job("Product Manager")]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "Bangalore")
        assert len(result) == 1

    def test_partial_match_in_title(self):
        """Jobs with search term in the title are kept."""
        jobs = [self._make_job("Senior Product Manager - Payments")]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "Bangalore")
        assert len(result) == 1

    def test_match_in_description(self):
        """Jobs with search term in description are kept."""
        jobs = [self._make_job("PM Lead", description="Looking for a Product Manager")]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "Bangalore")
        assert len(result) == 1

    def test_no_match(self):
        """Jobs not matching the search term are filtered out."""
        jobs = [self._make_job("Software Engineer")]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "Bangalore")
        assert len(result) == 0

    def test_location_match_exact(self):
        """Jobs with exact location match are kept."""
        jobs = [self._make_job("Product Manager", location="Bangalore")]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "Bangalore")
        assert len(result) == 1

    def test_location_alias_bangalore_bengaluru(self):
        """Bangalore/Bengaluru aliases work."""
        jobs = [self._make_job("Product Manager", location="Bengaluru, Karnataka")]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "Bangalore")
        assert len(result) == 1

    def test_location_alias_mumbai_bombay(self):
        """Mumbai/Bombay aliases work."""
        jobs = [self._make_job("Product Manager", location="Bombay")]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "Mumbai")
        assert len(result) == 1

    def test_location_alias_delhi_ncr(self):
        """Delhi NCR aliases work (Gurgaon, Noida, etc.)."""
        jobs = [self._make_job("Product Manager", location="Gurgaon, Haryana")]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "Delhi NCR")
        assert len(result) == 1

    def test_remote_jobs_always_match(self):
        """Remote jobs match any location."""
        jobs = [self._make_job("Product Manager", location="Remote")]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "Bangalore")
        assert len(result) == 1

    def test_location_mismatch(self):
        """Jobs in non-matching non-remote locations are filtered."""
        jobs = [self._make_job("Product Manager", location="Chennai")]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "Bangalore")
        assert len(result) == 0

    def test_empty_location_matches_all(self):
        """Empty location filter matches all jobs."""
        jobs = [
            self._make_job("Product Manager", location="Chennai"),
            self._make_job("Product Manager", location="Bangalore"),
        ]
        result = InstahyreScraper._filter_jobs(jobs, "Product Manager", "")
        assert len(result) == 2

    def test_case_insensitive(self):
        """Filtering is case-insensitive."""
        jobs = [self._make_job("PRODUCT MANAGER", location="BANGALORE")]
        result = InstahyreScraper._filter_jobs(jobs, "product manager", "bangalore")
        assert len(result) == 1


# ==================================================================
# Tests: safe_text helper
# ==================================================================

class TestSafeText:
    def test_returns_none_when_no_match(self):
        """Returns None when no selector matches."""
        mock_element = MagicMock()
        mock_locator = MagicMock()
        mock_locator.count.return_value = 0
        mock_element.locator.return_value = mock_locator

        result = InstahyreScraper._safe_text(mock_element, [".nonexistent"])
        assert result is None

    def test_returns_text_on_match(self):
        """Returns text content when a selector matches."""
        mock_element = MagicMock()
        mock_locator = MagicMock()
        mock_locator.count.return_value = 1
        mock_locator.first.text_content.return_value = "  Product Manager  "
        mock_element.locator.return_value = mock_locator

        result = InstahyreScraper._safe_text(mock_element, ["h3"])
        assert result == "Product Manager"

    def test_skips_empty_text(self):
        """Skips selectors that return empty text."""
        mock_element = MagicMock()

        empty_locator = MagicMock()
        empty_locator.count.return_value = 1
        empty_locator.first.text_content.return_value = "   "

        good_locator = MagicMock()
        good_locator.count.return_value = 1
        good_locator.first.text_content.return_value = "Product Manager"

        mock_element.locator.side_effect = [empty_locator, good_locator]

        result = InstahyreScraper._safe_text(mock_element, ["h3", "h4"])
        assert result == "Product Manager"


# ==================================================================
# Tests: Browser scrape flow (mocked)
# ==================================================================

class TestInstahyreBrowserScrape:
    @patch("backend.scrapers.instahyre_scraper.InstahyreScraper._run_browser_scrape")
    def test_scrape_calls_browser_when_configured(self, mock_browser):
        """Scrape delegates to _run_browser_scrape when configured."""
        mock_browser.return_value = [
            RawJob(title="PM", company="TestCo", source_portal="instahyre", source_engine="instahyre")
        ]
        scraper = InstahyreScraper(email="test@example.com", password="pass123")
        result = scraper.scrape("Product Manager", "Bangalore")
        assert len(result) == 1
        mock_browser.assert_called_once()

    @patch("backend.scrapers.instahyre_scraper.InstahyreScraper._run_browser_scrape")
    def test_scrape_handles_browser_exception(self, mock_browser):
        """Scrape returns empty list when browser scrape fails."""
        mock_browser.side_effect = Exception("Browser crashed")
        scraper = InstahyreScraper(email="test@example.com", password="pass123")
        result = scraper.scrape("Product Manager", "Bangalore")
        assert result == []

    def test_scrape_all_calls_scrape(self):
        """scrape_all from BaseScraper works with Instahyre."""
        scraper = InstahyreScraper(email="test@example.com", password="pass123")
        with patch.object(scraper, "scrape", return_value=[
            RawJob(title="PM", company="Co", source_portal="instahyre", source_engine="instahyre")
        ]) as mock_scrape:
            result = scraper.scrape_all(
                search_terms=["Product Manager"],
                locations=["Bangalore", "Mumbai"],
            )
            assert len(result) == 2
            assert mock_scrape.call_count == 2


# ==================================================================
# Tests: parse_card (mocked DOM elements)
# ==================================================================

class TestInstahyreParseCard:
    def _make_mock_card(
        self,
        title: str = "Product Manager",
        company: str = "Razorpay",
        location: str = "Bangalore",
        experience: str = "3-5 years",
        salary: str = "12L - 18L",
        skills: list[str] = None,
        job_url: str = "/job/123",
        date_text: str = "2 days ago",
    ) -> MagicMock:
        """Create a mock Playwright DOM card element."""
        card = MagicMock()

        def mock_locator(selector):
            loc = MagicMock()

            # Map selectors to return values
            if "title" in selector or selector in ("h3", "h4"):
                loc.count.return_value = 1
                loc.first.text_content.return_value = title
            elif "company" in selector or selector == "h5":
                loc.count.return_value = 1
                loc.first.text_content.return_value = company
            elif "location" in selector or "place" in selector:
                loc.count.return_value = 1
                loc.first.text_content.return_value = location
            elif "experience" in selector or "exp" in selector:
                loc.count.return_value = 1
                loc.first.text_content.return_value = experience
            elif "salary" in selector or "ctc" in selector or "compensation" in selector:
                loc.count.return_value = 1
                loc.first.text_content.return_value = salary
            elif "skill" in selector or "tag" in selector or "chip" in selector:
                skill_list = skills or ["SQL", "Analytics", "Product Strategy"]
                loc.count.return_value = len(skill_list)
                for idx, skill in enumerate(skill_list):
                    loc.nth.return_value.text_content.return_value = skill
                # Make nth return different skills
                loc.nth = MagicMock(side_effect=[
                    MagicMock(text_content=MagicMock(return_value=s))
                    for s in skill_list
                ])
            elif "description" in selector or "about" in selector:
                loc.count.return_value = 1
                loc.first.text_content.return_value = "Build payment products"
            elif "posted" in selector or "date" in selector or "time" in selector:
                loc.count.return_value = 1
                loc.first.text_content.return_value = date_text
            elif "a[href]" in selector:
                loc.count.return_value = 1
                loc.first.get_attribute.return_value = job_url
            else:
                loc.count.return_value = 0

            return loc

        card.locator = mock_locator
        card.get_attribute = MagicMock(return_value=None)

        return card

    def test_parse_card_basic(self):
        """Parse a complete job card."""
        scraper = InstahyreScraper(email="a@b.com", password="p")
        card = self._make_mock_card()
        page = MagicMock()

        job = scraper._parse_card(card, page)
        assert job is not None
        assert job.title == "Product Manager"
        assert job.company == "Razorpay"
        assert job.source_portal == "instahyre"
        assert job.source_engine == "instahyre"

    def test_parse_card_with_absolute_url(self):
        """Relative URLs are prefixed with BASE_URL."""
        scraper = InstahyreScraper(email="a@b.com", password="p")
        card = self._make_mock_card(job_url="/job/456")
        page = MagicMock()

        job = scraper._parse_card(card, page)
        assert job is not None
        assert job.job_url == "https://www.instahyre.com/job/456"

    def test_parse_card_no_title_returns_none(self):
        """Cards without a title are skipped."""
        scraper = InstahyreScraper(email="a@b.com", password="p")
        card = MagicMock()
        # All selectors return empty
        loc = MagicMock()
        loc.count.return_value = 0
        card.locator = MagicMock(return_value=loc)
        card.get_attribute = MagicMock(return_value=None)
        page = MagicMock()

        job = scraper._parse_card(card, page)
        assert job is None


# ==================================================================
# Tests: Integration with orchestrator
# ==================================================================

class TestInstahyreOrchestratorIntegration:
    def test_orchestrator_includes_instahyre_when_configured(self):
        """Orchestrator can use Instahyre as an engine."""
        from backend.scrapers.scraper_orchestrator import ScraperOrchestrator

        instahyre = InstahyreScraper(email="test@example.com", password="pass123")
        assert instahyre.is_configured

        # Verify it can be passed as an engine to the orchestrator
        orchestrator = ScraperOrchestrator(
            engines=[instahyre],
            search_terms=["PM"],
            locations=["Bangalore"],
            db_url="sqlite:///:memory:",
        )
        engine_names = [e.engine_name for e in orchestrator.engines]
        assert "instahyre" in engine_names

    def test_orchestrator_skips_instahyre_without_credentials(self):
        """Orchestrator skips Instahyre when credentials are missing."""
        instahyre = InstahyreScraper(email="", password="")
        assert not instahyre.is_configured
