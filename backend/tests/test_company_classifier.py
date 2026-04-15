"""
Unit tests for the company classifier module.

Tests curated list lookup, fuzzy matching, keyword heuristics, and domain bonuses.
"""

import pytest

from backend.scoring.company_classifier import (
    CompanyClassifier,
    _normalise,
    _load_fintech_companies,
    _load_banking_companies,
)


# ==================================================================
# Tests: Normalisation helper
# ==================================================================

class TestNormalise:
    def test_empty_string(self):
        assert _normalise("") == ""

    def test_strips_whitespace(self):
        assert _normalise("  Razorpay  ") == "razorpay"

    def test_lowercase(self):
        assert _normalise("HDFC Bank") == "hdfc bank"

    def test_removes_ltd(self):
        assert _normalise("Bajaj Finserv Ltd") == "bajaj finserv"

    def test_removes_pvt_ltd(self):
        assert _normalise("Razorpay Private Limited") == "razorpay"

    def test_removes_inc(self):
        assert _normalise("CRED Inc.") == "cred"

    def test_removes_technologies(self):
        assert _normalise("Karza Technologies") == "karza"

    def test_removes_india(self):
        assert _normalise("Fullerton India") == "fullerton"


# ==================================================================
# Tests: Data loading
# ==================================================================

class TestDataLoading:
    def test_fintech_companies_loaded(self):
        # Clear cache to test fresh load
        _load_fintech_companies.cache_clear()
        fintech = _load_fintech_companies()
        assert len(fintech) > 100  # Should have 170+ entries
        assert "razorpay" in fintech
        assert "phonepe" in fintech
        assert "cred" in fintech

    def test_banking_companies_loaded(self):
        _load_banking_companies.cache_clear()
        banking = _load_banking_companies()
        assert len(banking) > 50  # Should have 100+ entries
        assert "hdfc bank" in banking
        assert "icici bank" in banking
        assert banking["hdfc bank"] == "bank"

    def test_nbfc_companies_loaded(self):
        _load_banking_companies.cache_clear()
        banking = _load_banking_companies()
        assert "bajaj finance" in banking
        assert banking["bajaj finance"] == "nbfc"

    def test_digital_banking_arms_loaded(self):
        _load_banking_companies.cache_clear()
        banking = _load_banking_companies()
        assert "yono" in banking
        assert banking["yono"] == "digital_banking_arm"


# ==================================================================
# Tests: CompanyClassifier — exact matches
# ==================================================================

class TestClassifierExactMatch:
    def setup_method(self):
        self.classifier = CompanyClassifier()

    def test_fintech_exact(self):
        ctype, confidence = self.classifier.classify("Razorpay")
        assert ctype == "fintech"
        assert confidence == 1.0

    def test_fintech_case_insensitive(self):
        ctype, _ = self.classifier.classify("RAZORPAY")
        assert ctype == "fintech"

    def test_bank_exact(self):
        ctype, confidence = self.classifier.classify("HDFC Bank")
        assert ctype == "bank"
        assert confidence == 1.0

    def test_nbfc_exact(self):
        ctype, confidence = self.classifier.classify("Bajaj Finance")
        assert ctype == "nbfc"
        assert confidence == 1.0

    def test_digital_banking_arm_exact(self):
        ctype, confidence = self.classifier.classify("YONO")
        assert ctype == "digital_banking_arm"
        assert confidence == 1.0

    def test_fintech_with_suffix(self):
        """Companies with Ltd/Pvt should still match after normalisation."""
        ctype, _ = self.classifier.classify("Razorpay Private Limited")
        assert ctype == "fintech"

    def test_unknown_company(self):
        ctype, confidence = self.classifier.classify("Acme Widget Corp")
        assert ctype == "other"
        assert confidence < 0.85


# ==================================================================
# Tests: CompanyClassifier — fuzzy matching
# ==================================================================

class TestClassifierFuzzyMatch:
    def setup_method(self):
        self.classifier = CompanyClassifier()

    def test_slight_variation(self):
        """Small spelling variations should still fuzzy-match."""
        ctype, _ = self.classifier.classify("Razorpay Software")
        # Should fuzzy-match to Razorpay
        assert ctype == "fintech"

    def test_bank_variation(self):
        ctype, _ = self.classifier.classify("ICICI Bank Ltd")
        assert ctype == "bank"


# ==================================================================
# Tests: CompanyClassifier — keyword heuristic
# ==================================================================

class TestClassifierKeywordHeuristic:
    def setup_method(self):
        self.classifier = CompanyClassifier()

    def test_keyword_bank(self):
        ctype, confidence = self.classifier.classify("New Digital Bank XYZ")
        assert ctype == "bank"
        assert confidence == 0.6

    def test_keyword_fintech(self):
        ctype, confidence = self.classifier.classify("SuperPay Fintech Solutions")
        assert ctype == "fintech"
        assert confidence == 0.6

    def test_keyword_nbfc(self):
        ctype, confidence = self.classifier.classify("XYZ Microfinance Corp")
        assert ctype == "nbfc"
        assert confidence == 0.6


# ==================================================================
# Tests: CompanyClassifier — domain bonus
# ==================================================================

class TestDomainBonus:
    def setup_method(self):
        self.classifier = CompanyClassifier()

    def test_fintech_bonus(self):
        assert self.classifier.get_domain_bonus("fintech") == 15.0

    def test_bank_bonus(self):
        assert self.classifier.get_domain_bonus("bank") == 12.0

    def test_nbfc_bonus(self):
        assert self.classifier.get_domain_bonus("nbfc") == 10.0

    def test_digital_banking_arm_bonus(self):
        assert self.classifier.get_domain_bonus("digital_banking_arm") == 13.0

    def test_other_no_bonus(self):
        assert self.classifier.get_domain_bonus("other") == 0.0

    def test_unknown_type(self):
        assert self.classifier.get_domain_bonus("unknown") == 0.0


# ==================================================================
# Tests: CompanyClassifier — batch classification
# ==================================================================

class TestClassifierBatch:
    def test_batch(self):
        classifier = CompanyClassifier()
        results = classifier.classify_batch(["Razorpay", "HDFC Bank", "Acme Corp"])
        assert len(results) == 3
        assert results[0][0] == "fintech"
        assert results[1][0] == "bank"
        assert results[2][0] == "other"


# ==================================================================
# Tests: Edge cases
# ==================================================================

class TestClassifierEdgeCases:
    def setup_method(self):
        self.classifier = CompanyClassifier()

    def test_empty_string(self):
        ctype, confidence = self.classifier.classify("")
        assert ctype == "other"
        assert confidence == 0.0

    def test_none_like(self):
        ctype, _ = self.classifier.classify("   ")
        assert ctype == "other"

    def test_custom_maps(self):
        """Classifier works with custom lookup maps."""
        custom_classifier = CompanyClassifier(
            fintech_map={"testpay": "payments"},
            banking_map={"testbank": "bank"},
        )
        ctype, _ = custom_classifier.classify("TestPay")
        assert ctype == "fintech"

        ctype, _ = custom_classifier.classify("TestBank")
        assert ctype == "bank"
