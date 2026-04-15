"""
Unit tests for the deduplication engine.
"""

import pytest
from backend.scrapers.base_scraper import RawJob
from backend.utils.deduplicator import (
    _normalise,
    _normalise_title,
    compute_dedup_hash,
    deduplicate_jobs,
)


# ------------------------------------------------------------------
# Helper to build test RawJob objects quickly
# ------------------------------------------------------------------

def _job(title: str, company: str, location: str = "Bangalore", portal: str = "naukri") -> RawJob:
    return RawJob(
        title=title,
        company=company,
        location=location,
        source_portal=portal,
        source_engine="test",
    )


# ==================================================================
# Tests: _normalise
# ==================================================================

class TestNormalise:
    def test_basic(self):
        assert _normalise("  Hello   World  ") == "hello world"

    def test_special_chars(self):
        assert _normalise("Product Manager (Fintech)") == "product manager fintech"

    def test_none(self):
        assert _normalise(None) == ""

    def test_empty(self):
        assert _normalise("") == ""

    def test_unicode(self):
        result = _normalise("Müller & Associates")
        assert "müller" in result
        assert "associates" in result


# ==================================================================
# Tests: _normalise_title
# ==================================================================

class TestNormaliseTitle:
    def test_senior_abbreviation(self):
        assert "senior" in _normalise_title("Sr. Product Manager")

    def test_junior_abbreviation(self):
        assert "junior" in _normalise_title("Jr. Software Engineer")

    def test_manager_abbreviation(self):
        assert "manager" in _normalise_title("Product Mgr")

    def test_no_change(self):
        assert _normalise_title("Product Manager") == "product manager"


# ==================================================================
# Tests: compute_dedup_hash
# ==================================================================

class TestComputeDedupHash:
    def test_deterministic(self):
        """Same inputs always produce the same hash."""
        job = _job("Product Manager", "Razorpay", "Bangalore")
        h1 = compute_dedup_hash(job)
        h2 = compute_dedup_hash(job)
        assert h1 == h2

    def test_different_jobs_different_hash(self):
        """Different jobs produce different hashes."""
        j1 = _job("Product Manager", "Razorpay")
        j2 = _job("Product Manager", "PhonePe")
        assert compute_dedup_hash(j1) != compute_dedup_hash(j2)

    def test_case_insensitive(self):
        """Hashing is case-insensitive."""
        j1 = _job("PRODUCT MANAGER", "RAZORPAY", "BANGALORE")
        j2 = _job("product manager", "razorpay", "bangalore")
        assert compute_dedup_hash(j1) == compute_dedup_hash(j2)

    def test_abbreviation_expansion(self):
        """Sr. and Senior produce the same hash."""
        j1 = _job("Sr. Product Manager", "CRED")
        j2 = _job("Senior Product Manager", "CRED")
        assert compute_dedup_hash(j1) == compute_dedup_hash(j2)

    def test_whitespace_normalization(self):
        """Extra whitespace doesn't affect the hash."""
        j1 = _job("Product  Manager", "  Razorpay  ", "  Bangalore  ")
        j2 = _job("Product Manager", "Razorpay", "Bangalore")
        assert compute_dedup_hash(j1) == compute_dedup_hash(j2)

    def test_hash_is_sha256(self):
        """Hash should be a 64-character hex string (SHA-256)."""
        job = _job("Product Manager", "Test Company")
        h = compute_dedup_hash(job)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_different_location_different_hash(self):
        """Same role at same company but different location = different hash."""
        j1 = _job("PM", "Razorpay", "Bangalore")
        j2 = _job("PM", "Razorpay", "Mumbai")
        assert compute_dedup_hash(j1) != compute_dedup_hash(j2)


# ==================================================================
# Tests: deduplicate_jobs
# ==================================================================

class TestDeduplicateJobs:
    def test_no_duplicates(self):
        """All unique jobs should be preserved."""
        jobs = [
            _job("Product Manager", "Razorpay"),
            _job("Data Analyst", "PhonePe"),
            _job("Engineering Manager", "CRED"),
        ]
        result = deduplicate_jobs(jobs)
        assert len(result) == 3

    def test_exact_duplicates(self):
        """Exact duplicates from different portals should be removed."""
        jobs = [
            _job("Product Manager", "Razorpay", portal="naukri"),
            _job("Product Manager", "Razorpay", portal="linkedin"),
        ]
        result = deduplicate_jobs(jobs)
        assert len(result) == 1
        assert result[0].source_portal == "naukri"  # First occurrence kept

    def test_fuzzy_duplicates(self):
        """Near-identical titles should be caught."""
        jobs = [
            _job("Senior Product Manager", "Razorpay", portal="naukri"),
            _job("Sr. Product Manager", "Razorpay", portal="linkedin"),
        ]
        result = deduplicate_jobs(jobs)
        assert len(result) == 1

    def test_different_enough_kept(self):
        """Jobs that look similar but are legitimately different should be kept."""
        jobs = [
            _job("Product Manager - Payments", "Razorpay"),
            _job("Product Manager - Lending", "Razorpay"),
        ]
        result = deduplicate_jobs(jobs)
        # These are different roles, should both survive
        assert len(result) == 2

    def test_empty_input(self):
        """Empty list returns empty list."""
        assert deduplicate_jobs([]) == []

    def test_single_job(self):
        """Single job returns the same job."""
        jobs = [_job("PM", "Test")]
        result = deduplicate_jobs(jobs)
        assert len(result) == 1

    def test_cross_engine_dedup(self):
        """Jobs from different engines for the same role should be deduplicated."""
        j1 = RawJob(title="Product Manager", company="HDFC Bank", source_engine="jobspy", source_portal="naukri")
        j2 = RawJob(title="Product Manager", company="HDFC Bank", source_engine="apify", source_portal="naukri")
        result = deduplicate_jobs([j1, j2])
        assert len(result) == 1

    def test_preserves_order(self):
        """First occurrence should always be kept."""
        jobs = [
            _job("PM", "CompanyA", portal="linkedin"),
            _job("Data Scientist", "CompanyB", portal="naukri"),
            _job("PM", "CompanyA", portal="naukri"),
        ]
        result = deduplicate_jobs(jobs)
        assert len(result) == 2
        assert result[0].source_portal == "linkedin"

    def test_threshold_sensitivity(self):
        """Higher threshold should keep more jobs (stricter matching)."""
        jobs = [
            _job("Product Manager", "Razorpay"),
            _job("Product Management Lead", "Razorpay"),
        ]
        # With default threshold (85) these might be different enough
        result_default = deduplicate_jobs(jobs, similarity_threshold=85)
        # With very low threshold, almost everything is a "duplicate"
        result_strict = deduplicate_jobs(jobs, similarity_threshold=50)
        assert len(result_default) >= len(result_strict)
