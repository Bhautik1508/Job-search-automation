"""
Unit tests for Apify Actor tuning (Phase 4.5).

Tests verify:
  - Portal-specific actor input generation (LinkedIn, Naukri, Indeed, Glassdoor)
  - Banking-specific search query injection
  - Credit usage monitoring and threshold warnings
  - Configurable timeout and max-items
  - scrape_all behavior with and without banking queries
"""

import sys
import pytest
from unittest.mock import patch, MagicMock, call
from datetime import datetime

# Pre-mock the apify_client module since the installed version has a broken
# dependency (apify_shared.utils.ignore_docs). This allows us to test our
# code without needing a working apify_client installation.
mock_apify_client = MagicMock()
sys.modules["apify_client"] = mock_apify_client

from backend.scrapers.base_scraper import RawJob
from backend.scrapers.apify_scraper import ApifyScraper



# ==================================================================
# Tests: Actor Input Building (Portal-Specific Tuning)
# ==================================================================

class TestActorInputBuilding:
    def setup_method(self):
        self.scraper = ApifyScraper(
            api_token="test_token",
            actors={
                "linkedin": "actor_linkedin",
                "naukri": "actor_naukri",
                "indeed": "actor_indeed",
                "glassdoor": "actor_glassdoor",
            },
            max_items=50,
        )

    def test_linkedin_input_has_search_url(self):
        """LinkedIn actor input includes searchUrl with keywords and location."""
        inp = self.scraper._build_actor_input("linkedin", "Product Manager", "Bangalore", 30)
        assert "searchUrl" in inp
        assert "Product Manager" in inp["searchUrl"]
        assert "Bangalore" in inp["searchUrl"]

    def test_linkedin_input_has_freshness_filter(self):
        """LinkedIn actor input has time-based filter for fresh results."""
        inp = self.scraper._build_actor_input("linkedin", "PM", "Mumbai", 30)
        assert "f_TPR" in inp["searchUrl"]  # Time posted range

    def test_linkedin_input_has_proxy(self):
        """LinkedIn actor input uses residential proxies."""
        inp = self.scraper._build_actor_input("linkedin", "PM", "Delhi", 30)
        assert "proxy" in inp
        assert inp["proxy"]["useApifyProxy"] is True

    def test_linkedin_input_limits_pages(self):
        """LinkedIn actor input limits max pages to conserve credits."""
        inp = self.scraper._build_actor_input("linkedin", "PM", "Delhi", 30)
        assert "maxPages" in inp
        assert inp["maxPages"] <= 5

    def test_naukri_input_has_keyword(self):
        """Naukri actor input uses 'keyword' field."""
        inp = self.scraper._build_actor_input("naukri", "Product Manager", "Pune", 30)
        assert inp["keyword"] == "Product Manager"
        assert inp["location"] == "Pune"

    def test_naukri_input_has_experience_range(self):
        """Naukri actor input has PM-level experience range."""
        inp = self.scraper._build_actor_input("naukri", "PM", "Bangalore", 30)
        assert "experience" in inp

    def test_naukri_input_sorts_by_date(self):
        """Naukri actor input sorts by date for freshness."""
        inp = self.scraper._build_actor_input("naukri", "PM", "Bangalore", 30)
        assert inp.get("sortBy") == "date"

    def test_naukri_input_freshness_filter(self):
        """Naukri actor input filters by freshness."""
        inp = self.scraper._build_actor_input("naukri", "PM", "Mumbai", 30)
        assert "freshness" in inp

    def test_indeed_input_has_query(self):
        """Indeed actor input uses 'query' field."""
        inp = self.scraper._build_actor_input("indeed", "Product Manager", "Delhi", 30)
        assert inp["query"] == "Product Manager"
        assert inp["country"] == "IN"

    def test_indeed_input_sorts_by_date(self):
        """Indeed actor input sorts by date."""
        inp = self.scraper._build_actor_input("indeed", "PM", "Hyderabad", 30)
        assert inp.get("sort") == "date"

    def test_indeed_input_has_freshness(self):
        """Indeed actor input has 'fromage' for freshness."""
        inp = self.scraper._build_actor_input("indeed", "PM", "Pune", 30)
        assert "fromage" in inp

    def test_glassdoor_input_has_keyword(self):
        """Glassdoor actor input uses 'keyword' field."""
        inp = self.scraper._build_actor_input("glassdoor", "Product Manager", "Mumbai", 30)
        assert inp["keyword"] == "Product Manager"

    def test_glassdoor_input_country(self):
        """Glassdoor actor input specifies India."""
        inp = self.scraper._build_actor_input("glassdoor", "PM", "Delhi", 30)
        assert inp.get("country") == "India"

    def test_unknown_portal_fallback(self):
        """Unknown portal gets generic input."""
        inp = self.scraper._build_actor_input("newportal", "PM", "Delhi", 30)
        assert inp["keywords"] == "PM"
        assert inp["location"] == "Delhi"

    def test_max_items_capped(self):
        """Max items is capped by scraper's max_items config."""
        scraper = ApifyScraper(api_token="test", max_items=20)
        inp = scraper._build_actor_input("linkedin", "PM", "Delhi", 100)
        assert inp["maxItems"] == 20  # Capped at 20, not 100

    def test_max_items_uses_smaller(self):
        """Uses the smaller of requested and configured max_items."""
        scraper = ApifyScraper(api_token="test", max_items=50)
        inp = scraper._build_actor_input("naukri", "PM", "Delhi", 30)
        assert inp["maxItems"] == 30  # 30 < 50, so use 30


# ==================================================================
# Tests: Banking-Specific Queries
# ==================================================================

class TestBankingQueries:
    def test_banking_queries_enabled_by_default(self):
        """Banking queries are enabled when enable_banking_queries is True."""
        scraper = ApifyScraper(
            api_token="test",
            enable_banking_queries=True,
        )
        assert scraper.enable_banking_queries is True

    def test_banking_queries_can_be_disabled(self):
        """Banking queries can be explicitly disabled."""
        scraper = ApifyScraper(
            api_token="test",
            enable_banking_queries=False,
        )
        assert scraper.enable_banking_queries is False

    @patch.object(ApifyScraper, "scrape")
    @patch.object(ApifyScraper, "_check_credit_usage")
    def test_scrape_all_runs_banking_queries_when_enabled(self, mock_credit, mock_scrape):
        """scrape_all runs banking queries when enabled."""
        mock_scrape.return_value = []
        mock_credit.return_value = None

        scraper = ApifyScraper(
            api_token="test",
            enable_banking_queries=True,
        )
        scraper.scrape_all(
            search_terms=["Product Manager"],
            locations=["Bangalore"],
        )

        # Base search (1 term × 1 location) + banking queries (7 terms × 1 location)
        total_calls = mock_scrape.call_count
        assert total_calls > 1  # More than just the base query
        assert total_calls == 8  # 1 base + 7 banking

    @patch.object(ApifyScraper, "scrape")
    @patch.object(ApifyScraper, "_check_credit_usage")
    def test_scrape_all_skips_banking_queries_when_disabled(self, mock_credit, mock_scrape):
        """scrape_all does NOT run banking queries when disabled."""
        mock_scrape.return_value = []
        mock_credit.return_value = None

        scraper = ApifyScraper(
            api_token="test",
            enable_banking_queries=False,
        )
        scraper.scrape_all(
            search_terms=["Product Manager"],
            locations=["Bangalore"],
        )

        # Only base search (1 term × 1 location)
        assert mock_scrape.call_count == 1

    @patch.object(ApifyScraper, "scrape")
    @patch.object(ApifyScraper, "_check_credit_usage")
    def test_scrape_all_with_multiple_locations(self, mock_credit, mock_scrape):
        """scrape_all runs across all locations for both base and banking queries."""
        mock_scrape.return_value = []
        mock_credit.return_value = None

        scraper = ApifyScraper(
            api_token="test",
            enable_banking_queries=True,
        )
        scraper.scrape_all(
            search_terms=["Product Manager", "Senior PM"],
            locations=["Bangalore", "Mumbai"],
        )

        # Base: 2 terms × 2 locations = 4
        # Banking: 7 terms × 2 locations = 14
        assert mock_scrape.call_count == 18

    @patch.object(ApifyScraper, "scrape")
    @patch.object(ApifyScraper, "_check_credit_usage")
    def test_banking_queries_include_expected_terms(self, mock_credit, mock_scrape):
        """Banking queries include fintech/banking-specific search terms."""
        mock_scrape.return_value = []
        mock_credit.return_value = None

        scraper = ApifyScraper(
            api_token="test",
            enable_banking_queries=True,
        )
        scraper.scrape_all(
            search_terms=["PM"],
            locations=["Bangalore"],
        )

        # Collect all search terms passed to scrape()
        search_terms_used = [c.args[0] for c in mock_scrape.call_args_list]

        assert "PM" in search_terms_used  # Base term
        assert any("Fintech" in t for t in search_terms_used)   # Banking query
        assert any("Banking" in t for t in search_terms_used)   # Banking query
        assert any("Payment" in t for t in search_terms_used)   # Banking query


# ==================================================================
# Tests: Credit Usage Monitoring
# ==================================================================

class TestCreditMonitoring:
    def test_credit_check_returns_none_without_token(self):
        """Credit check returns None when not configured."""
        scraper = ApifyScraper(api_token="")
        assert scraper.check_credit_balance() is None

    @patch("apify_client.ApifyClient")
    def test_credit_check_parses_user_info(self, mock_client_cls):
        """Credit check correctly parses user info from API."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.user.return_value.get.return_value = {
            "plan": {"monthlyUsageCreditsUsd": 5.0},
            "usage": {"monthlyUsageCreditsUsd": 2.0},
        }

        scraper = ApifyScraper(api_token="test_token", credit_warning_threshold=0.5)
        result = scraper.check_credit_balance()

        assert result is not None
        assert result["total_credits"] == 5.0
        assert result["used_credits"] == 2.0
        assert result["remaining_credits"] == 3.0
        assert result["usage_fraction"] == 0.4
        assert result["warning"] is False  # 0.4 < 0.5

    @patch("apify_client.ApifyClient")
    def test_credit_warning_when_over_threshold(self, mock_client_cls):
        """Warning is True when usage exceeds threshold."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.user.return_value.get.return_value = {
            "plan": {"monthlyUsageCreditsUsd": 5.0},
            "usage": {"monthlyUsageCreditsUsd": 3.5},
        }

        scraper = ApifyScraper(api_token="test_token", credit_warning_threshold=0.5)
        result = scraper.check_credit_balance()

        assert result["warning"] is True  # 0.7 >= 0.5

    @patch("apify_client.ApifyClient")
    def test_credit_usage_log_accumulates(self, mock_client_cls):
        """Credit usage log tracks all checks."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.user.return_value.get.return_value = {
            "plan": {"monthlyUsageCreditsUsd": 5.0},
            "usage": {"monthlyUsageCreditsUsd": 1.0},
        }

        scraper = ApifyScraper(api_token="test_token")
        scraper.check_credit_balance()
        scraper.check_credit_balance()

        log = scraper.get_credit_usage_log()
        assert len(log) == 2

    @patch("apify_client.ApifyClient")
    def test_credit_check_handles_api_error(self, mock_client_cls):
        """Credit check handles API errors gracefully."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client
        mock_client.user.return_value.get.side_effect = Exception("API Error")

        scraper = ApifyScraper(api_token="test_token")
        result = scraper.check_credit_balance()
        assert result is None

    def test_credit_warning_threshold_configurable(self):
        """Credit warning threshold can be configured."""
        scraper = ApifyScraper(api_token="test", credit_warning_threshold=0.8)
        assert scraper.credit_warning_threshold == 0.8


# ==================================================================
# Tests: Timeout and Max Items Configuration
# ==================================================================

class TestApifyConfiguration:
    def test_default_timeout(self):
        """Default timeout is applied."""
        scraper = ApifyScraper(api_token="test")
        assert scraper.timeout_secs > 0

    def test_custom_timeout(self):
        """Custom timeout overrides default."""
        scraper = ApifyScraper(api_token="test", timeout_secs=300)
        assert scraper.timeout_secs == 300

    def test_default_max_items(self):
        """Default max_items is applied."""
        scraper = ApifyScraper(api_token="test")
        assert scraper.max_items > 0

    def test_custom_max_items(self):
        """Custom max_items overrides default."""
        scraper = ApifyScraper(api_token="test", max_items=100)
        assert scraper.max_items == 100

    def test_not_configured_without_token(self):
        """Scraper is not configured without API token."""
        scraper = ApifyScraper(api_token="")
        assert not scraper.is_configured

    def test_configured_with_token(self):
        """Scraper is configured with API token."""
        scraper = ApifyScraper(api_token="test_token_123")
        assert scraper.is_configured

    def test_scrape_returns_empty_without_token(self):
        """Scrape returns empty when not configured."""
        scraper = ApifyScraper(api_token="")
        result = scraper.scrape("PM", "Bangalore")
        assert result == []


# ==================================================================
# Tests: Item to RawJob conversion (existing tests + new ones)
# ==================================================================

class TestItemToRawJob:
    def test_basic_conversion(self):
        """Basic item converts correctly."""
        scraper = ApifyScraper(api_token="test")
        item = {
            "title": "Product Manager",
            "company": "CRED",
            "location": "Bangalore",
            "description": "Build credit products",
            "url": "https://cred.club/careers/pm",
            "postedAt": "2025-01-15T10:00:00Z",
            "salary": {"min": 2000000, "max": 3000000},
            "experience": "3-5 years",
            "jobType": "full-time",
        }
        job = scraper._item_to_raw_job(item, "linkedin")
        assert job.title == "Product Manager"
        assert job.company == "CRED"
        assert job.source_portal == "linkedin"
        assert job.source_engine == "apify"
        assert job.salary_min == 2000000
        assert job.salary_max == 3000000

    def test_alternate_field_names(self):
        """Different actor field names are handled."""
        scraper = ApifyScraper(api_token="test")
        item = {
            "jobTitle": "Senior PM",
            "companyName": "Groww",
            "jobLocation": "Mumbai",
            "jobDescription": "Lead product",
            "link": "https://groww.in/pm",
        }
        job = scraper._item_to_raw_job(item, "glassdoor")
        assert job.title == "Senior PM"
        assert job.company == "Groww"
        assert job.location == "Mumbai"

    def test_salary_string_parsing(self):
        """Salary as string is parsed into min/max."""
        scraper = ApifyScraper(api_token="test")
        item = {
            "title": "PM",
            "company": "Test",
            "salary": "1200000-1800000",
        }
        job = scraper._item_to_raw_job(item, "naukri")
        assert job.salary_min == 1200000
        assert job.salary_max == 1800000

    def test_missing_fields_dont_crash(self):
        """Items with missing fields don't crash."""
        scraper = ApifyScraper(api_token="test")
        item = {}
        job = scraper._item_to_raw_job(item, "linkedin")
        assert job.title == ""
        assert job.company == ""
        assert job.location is None

    def test_date_parsing_iso(self):
        """ISO date strings are parsed correctly."""
        scraper = ApifyScraper(api_token="test")
        item = {
            "title": "PM",
            "company": "Test",
            "postedAt": "2025-01-15T10:00:00Z",
        }
        job = scraper._item_to_raw_job(item, "naukri")
        assert job.date_posted is not None
        assert job.date_posted.year == 2025

    def test_date_parsing_datetime_object(self):
        """datetime objects pass through correctly."""
        scraper = ApifyScraper(api_token="test")
        dt = datetime(2025, 3, 15, 12, 0, 0)
        item = {
            "title": "PM",
            "company": "Test",
            "datePosted": dt,
        }
        job = scraper._item_to_raw_job(item, "indeed")
        assert job.date_posted == dt

    def test_extra_field_preserved(self):
        """Original item is stored in 'extra' for debugging."""
        scraper = ApifyScraper(api_token="test")
        item = {"title": "PM", "company": "Test", "custom_field": "value"}
        job = scraper._item_to_raw_job(item, "linkedin")
        assert job.extra == item
        assert job.extra["custom_field"] == "value"


# ==================================================================
# Tests: _run_actor with mocked ApifyClient
# ==================================================================

class TestRunActor:
    @patch("apify_client.ApifyClient")
    def test_run_actor_uses_tuned_input(self, mock_client_cls):
        """_run_actor uses the portal-specific tuned input."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_run = {"defaultDatasetId": "dataset_123"}
        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.dataset.return_value.iterate_items.return_value = [
            {"title": "PM", "company": "TestCo", "location": "Bangalore"}
        ]

        scraper = ApifyScraper(api_token="test", timeout_secs=180, max_items=50)
        jobs = scraper._run_actor("actor_id", "linkedin", "PM", "Bangalore", 30)

        # Verify the actor was called with tuned input
        call_args = mock_client.actor.return_value.call.call_args
        assert call_args.kwargs["timeout_secs"] == 180

        # Verify jobs were returned
        assert len(jobs) == 1
        assert jobs[0].title == "PM"

    @patch("apify_client.ApifyClient")
    def test_run_actor_with_timeout(self, mock_client_cls):
        """_run_actor respects timeout_secs setting."""
        mock_client = MagicMock()
        mock_client_cls.return_value = mock_client

        mock_run = {"defaultDatasetId": "ds"}
        mock_client.actor.return_value.call.return_value = mock_run
        mock_client.dataset.return_value.iterate_items.return_value = []

        scraper = ApifyScraper(api_token="test", timeout_secs=240)
        scraper._run_actor("actor_id", "naukri", "PM", "Delhi", 30)

        call_kwargs = mock_client.actor.return_value.call.call_args.kwargs
        assert call_kwargs["timeout_secs"] == 240
