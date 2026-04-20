"""
Unit tests for the Phase 6 tier classifier.

Covers exact-match lookup, fuzzy fallback, startup heuristic, tier bonuses,
and the careers-links registry convenience function.
"""

from __future__ import annotations

from backend.scoring.tier_classifier import (
    TierClassifier,
    TierProfile,
    careers_links,
)


# Small in-memory registry used across most tests — keeps fixtures deterministic
# regardless of whether data/company_tiers.json grows later.
_REGISTRY = {
    "razorpay": TierProfile(
        tier="unicorn", stage="series_f", headcount="1000-5000",
        careers_url="https://razorpay.com/jobs", confidence=1.0,
    ),
    "google": TierProfile(
        tier="top_tier", stage="public", headcount="5000+",
        careers_url="https://careers.google.com", confidence=1.0,
    ),
    "jupiter": TierProfile(
        tier="growth_startup", stage="series_c", headcount="200-1000",
        careers_url=None, confidence=1.0,
    ),
}


# ==================================================================
# Exact + fuzzy matching
# ==================================================================

class TestClassifyExact:
    def test_empty_name_returns_other(self):
        clf = TierClassifier(registry=_REGISTRY)
        result = clf.classify("")
        assert result.tier == "other"
        assert result.confidence == 0.0

    def test_whitespace_only_returns_other(self):
        clf = TierClassifier(registry=_REGISTRY)
        assert clf.classify("   ").tier == "other"

    def test_exact_match(self):
        clf = TierClassifier(registry=_REGISTRY)
        result = clf.classify("Razorpay")
        assert result.tier == "unicorn"
        assert result.stage == "series_f"
        assert result.headcount == "1000-5000"
        assert result.confidence == 1.0

    def test_exact_match_is_case_insensitive(self):
        clf = TierClassifier(registry=_REGISTRY)
        assert clf.classify("razorpay").tier == "unicorn"
        assert clf.classify("RAZORPAY").tier == "unicorn"

    def test_strips_suffix_during_lookup(self):
        clf = TierClassifier(registry=_REGISTRY)
        # _normalise drops "Pvt Ltd" so this should still hit the exact match.
        assert clf.classify("Razorpay Pvt Ltd").tier == "unicorn"


class TestClassifyFuzzy:
    def test_close_typo_hits_fuzzy_match(self):
        # fuzzy_threshold=90 — "razorpy" vs "razorpay" is well above that.
        clf = TierClassifier(registry=_REGISTRY, fuzzy_threshold=85)
        result = clf.classify("Razorpy")
        assert result.tier == "unicorn"
        assert 0.85 <= result.confidence < 1.0

    def test_below_threshold_falls_through(self):
        # Very different name — no match, no startup tokens → other.
        clf = TierClassifier(registry=_REGISTRY, fuzzy_threshold=95)
        result = clf.classify("TotallyUnrelatedCorp")
        assert result.tier == "other"


class TestStartupHeuristic:
    def test_dot_ai_is_early_startup(self):
        clf = TierClassifier(registry=_REGISTRY)
        result = clf.classify("Foobar.ai")
        assert result.tier == "early_startup"
        assert result.confidence == 0.3

    def test_labs_suffix_is_early_startup(self):
        clf = TierClassifier(registry=_REGISTRY)
        assert clf.classify("Acme Labs").tier == "early_startup"

    def test_technologies_suffix_is_early_startup(self):
        clf = TierClassifier(registry=_REGISTRY)
        # "Someco Technologies" — " technologies" in lower → startup heuristic.
        assert clf.classify("Someco Technologies").tier == "early_startup"

    def test_plain_word_is_other(self):
        clf = TierClassifier(registry=_REGISTRY)
        assert clf.classify("Johnson").tier == "other"


# ==================================================================
# Tier bonus
# ==================================================================

class TestTierBonus:
    def test_bonus_values(self):
        clf = TierClassifier(registry=_REGISTRY)
        assert clf.get_tier_bonus("top_tier") == 10.0
        assert clf.get_tier_bonus("unicorn") == 7.0
        assert clf.get_tier_bonus("growth_startup") == 4.0
        assert clf.get_tier_bonus("early_startup") == 2.0
        assert clf.get_tier_bonus("other") == 0.0

    def test_unknown_tier_returns_zero(self):
        clf = TierClassifier(registry=_REGISTRY)
        assert clf.get_tier_bonus("made_up") == 0.0


# ==================================================================
# Registry loader & careers_links
# ==================================================================

class TestRealRegistry:
    """Smoke tests against data/company_tiers.json — guards against the file
    going missing or losing well-known anchor entries."""

    def test_default_registry_loads(self):
        clf = TierClassifier()
        assert len(clf.registry) > 0

    def test_google_is_top_tier(self):
        clf = TierClassifier()
        assert clf.classify("Google").tier == "top_tier"

    def test_razorpay_is_unicorn(self):
        clf = TierClassifier()
        assert clf.classify("Razorpay").tier == "unicorn"


class TestCareersLinks:
    def test_returns_list_of_dicts_with_urls(self):
        links = careers_links()
        assert isinstance(links, list)
        assert len(links) > 0
        for entry in links:
            assert set(entry.keys()) == {"name", "tier", "careers_url"}
            assert entry["careers_url"]  # non-empty

    def test_dedupes_case_variants(self):
        # Registry has both "Cred" and "CRED" — careers_links should surface
        # only one of them (normalised dedup).
        links = careers_links()
        names_normalised = [entry["name"].lower() for entry in links]
        assert names_normalised.count("cred") == 1
