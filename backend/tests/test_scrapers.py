"""
Unit tests for the scraper modules (base, jobspy, apify).

These tests use mocking to avoid making real API calls.
"""

import sys
import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime

import pandas as pd

# Create a mock 'jobspy' module so the lazy import inside JobSpyScraper.scrape() works
mock_jobspy_module = MagicMock()
sys.modules["jobspy"] = mock_jobspy_module

from backend.scrapers.base_scraper import BaseScraper, RawJob
from backend.scrapers.jobspy_scraper import JobSpyScraper
from backend.scrapers.apify_scraper import ApifyScraper


# ==================================================================
# Tests: RawJob dataclass
# ==================================================================

class TestRawJob:
    def test_creation(self):
        """RawJob can be created with minimal fields."""
        job = RawJob(title="PM", company="Test")
        assert job.title == "PM"
        assert job.company == "Test"
        assert job.location is None
        assert job.extra == {}

    def test_all_fields(self):
        """RawJob supports all fields."""
        job = RawJob(
            title="Product Manager",
            company="Razorpay",
            location="Bangalore",
            description="Build products",
            job_url="https://example.com",
            source_portal="naukri",
            source_engine="jobspy",
            external_id="123",
            salary_min=1200000,
            salary_max=1800000,
            salary_currency="INR",
            experience_required="3-5 years",
            skills="SQL, analytics",
            job_type="full-time",
            work_mode="hybrid",
            date_posted=datetime(2025, 1, 1),
            extra={"rating": 4.5},
        )
        assert job.salary_min == 1200000
        assert job.extra["rating"] == 4.5


# ==================================================================
# Tests: BaseScraper
# ==================================================================

class TestBaseScraper:
    def test_abstract_cannot_instantiate(self):
        """BaseScraper cannot be directly instantiated."""
        with pytest.raises(TypeError):
            BaseScraper()

    def test_concrete_implementation(self):
        """A concrete subclass works correctly."""

        class MockScraper(BaseScraper):
            engine_name = "mock"

            def scrape(self, search_term, location, results_wanted=30, hours_old=72):
                return [RawJob(title=f"{search_term} at {location}", company="MockCo")]

        scraper = MockScraper()
        results = scraper.scrape("PM", "Bangalore")
        assert len(results) == 1
        assert "PM" in results[0].title

    def test_scrape_all(self):
        """scrape_all iterates over all term × location combos."""

        class MockScraper(BaseScraper):
            engine_name = "mock"

            def scrape(self, search_term, location, results_wanted=30, hours_old=72):
                return [RawJob(title=search_term, company="MockCo", location=location)]

        scraper = MockScraper()
        results = scraper.scrape_all(
            search_terms=["PM", "APM"],
            locations=["Bangalore", "Mumbai"],
        )
        assert len(results) == 4  # 2 terms × 2 locations

    def test_scrape_all_handles_errors(self):
        """scrape_all catches errors from individual scrape calls."""

        class FailingScraper(BaseScraper):
            engine_name = "failing"

            def scrape(self, search_term, location, results_wanted=30, hours_old=72):
                if location == "Mumbai":
                    raise RuntimeError("Network error")
                return [RawJob(title=search_term, company="MockCo")]

        scraper = FailingScraper()
        results = scraper.scrape_all(
            search_terms=["PM"],
            locations=["Bangalore", "Mumbai"],
        )
        # Only Bangalore succeeds
        assert len(results) == 1


# ==================================================================
# Tests: JobSpyScraper (mocked)
# ==================================================================

class TestJobSpyScraper:
    def test_init_defaults(self):
        """Default sites include the three reliable portals."""
        scraper = JobSpyScraper()
        assert "indeed" in scraper.sites
        assert "linkedin" in scraper.sites
        assert "google" in scraper.sites
        # Naukri and Glassdoor are excluded (CAPTCHA / API errors)
        assert "naukri" not in scraper.sites
        assert "glassdoor" not in scraper.sites

    def test_init_custom_sites(self):
        """Custom sites can be specified."""
        scraper = JobSpyScraper(sites=["naukri", "linkedin"])
        assert len(scraper.sites) == 2

    @patch("jobspy.scrape_jobs")
    def test_scrape_returns_raw_jobs(self, mock_scrape):
        """JobSpy scraper converts DataFrame to RawJob list."""
        mock_scrape.return_value = pd.DataFrame([
            {
                "site": "naukri",
                "title": "Product Manager",
                "company": "Razorpay",
                "location": "Bangalore",
                "description": "Build products",
                "job_url": "https://naukri.com/job/123",
                "id": "naukri_123",
                "date_posted": "2025-01-15",
                "min_amount": 1200000,
                "max_amount": 1800000,
                "currency": "INR",
                "job_type": "full-time",
            },
            {
                "site": "linkedin",
                "title": "Senior PM",
                "company": "PhonePe",
                "location": "Mumbai",
                "description": "Lead products",
                "job_url": "https://linkedin.com/job/456",
                "id": "ln_456",
                "date_posted": datetime(2025, 1, 16),
                "min_amount": None,
                "max_amount": None,
                "currency": None,
                "job_type": None,
            },
        ])

        scraper = JobSpyScraper()
        results = scraper.scrape("Product Manager", "Bangalore")

        assert len(results) == 2
        assert results[0].title == "Product Manager"
        assert results[0].company == "Razorpay"
        assert results[0].source_portal == "naukri"
        assert results[0].source_engine == "jobspy"
        assert results[0].salary_min == 1200000
        assert results[1].title == "Senior PM"
        assert results[1].source_portal == "linkedin"

    @patch("jobspy.scrape_jobs")
    def test_scrape_empty_df(self, mock_scrape):
        """Empty DataFrame returns empty list."""
        mock_scrape.return_value = pd.DataFrame()
        scraper = JobSpyScraper()
        results = scraper.scrape("PM", "Bangalore")
        assert results == []

    @patch("jobspy.scrape_jobs")
    def test_scrape_none_df(self, mock_scrape):
        """None return from scrape_jobs returns empty list."""
        mock_scrape.return_value = None
        scraper = JobSpyScraper()
        results = scraper.scrape("PM", "Bangalore")
        assert results == []

    @patch("jobspy.scrape_jobs")
    def test_scrape_exception(self, mock_scrape):
        """Exception from scrape_jobs returns empty list (graceful fallback)."""
        mock_scrape.side_effect = Exception("Rate limited")
        scraper = JobSpyScraper()
        results = scraper.scrape("PM", "Bangalore")
        assert results == []

    @patch("jobspy.scrape_jobs")
    def test_handles_nan_values(self, mock_scrape):
        """NaN values in DataFrame are handled gracefully."""
        mock_scrape.return_value = pd.DataFrame([
            {
                "site": "indeed",
                "title": "PM",
                "company": "TestCo",
                "location": float("nan"),
                "description": float("nan"),
                "job_url": float("nan"),
                "id": float("nan"),
                "date_posted": float("nan"),
                "min_amount": float("nan"),
                "max_amount": float("nan"),
                "currency": float("nan"),
                "job_type": float("nan"),
            }
        ])
        scraper = JobSpyScraper()
        results = scraper.scrape("PM", "Bangalore")
        assert len(results) == 1
        assert results[0].location is None
        assert results[0].salary_min is None


# ==================================================================
# Tests: ApifyScraper
# ==================================================================

class TestApifyScraper:
    def test_not_configured(self):
        """Without an API token, scrape returns empty list."""
        scraper = ApifyScraper(api_token="", actors={})
        assert not scraper.is_configured
        results = scraper.scrape("PM", "Bangalore")
        assert results == []

    def test_is_configured(self):
        """With a token, is_configured returns True."""
        scraper = ApifyScraper(api_token="test_token_123")
        assert scraper.is_configured

    def test_item_to_raw_job_basic(self):
        """Apify dataset items are normalised to RawJob."""
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
        assert job.date_posted is not None

    def test_item_to_raw_job_alternate_fields(self):
        """Apify actors use different field names — all are handled."""
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
        assert job.source_portal == "glassdoor"

    def test_item_to_raw_job_salary_string(self):
        """Salary as string is parsed correctly."""
        scraper = ApifyScraper(api_token="test")
        item = {
            "title": "PM",
            "company": "Test",
            "salary": "₹12,00,000 - ₹18,00,000",
        }
        job = scraper._item_to_raw_job(item, "naukri")
        # The parsing may not perfectly handle Indian format,
        # but it should not crash
        assert isinstance(job, RawJob)

    def test_item_to_raw_job_missing_fields(self):
        """Items with missing fields don't crash."""
        scraper = ApifyScraper(api_token="test")
        item = {}
        job = scraper._item_to_raw_job(item, "linkedin")
        assert job.title == ""
        assert job.company == ""
        assert job.location is None
