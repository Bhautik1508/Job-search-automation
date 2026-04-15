"""
Unit tests for the Gemini scorer module.

All Gemini API calls are mocked to avoid real API usage.
"""

import json
import time
import pytest
from unittest.mock import patch, MagicMock

from backend.scoring.gemini_scorer import (
    GeminiScorer,
    JobScoreResult,
    ScoringWeights,
)


# ==================================================================
# Tests: JobScoreResult Pydantic model
# ==================================================================

class TestJobScoreResult:
    def test_valid_creation(self):
        result = JobScoreResult(
            skills_match=80,
            domain_fit=90,
            experience_match=75,
            seniority_match=85,
            missing_skills=["SQL", "Tableau"],
            verdict="STRONG_FIT",
            apply_priority="APPLY_NOW",
            reasoning="Strong match with fintech experience.",
        )
        assert result.skills_match == 80
        assert result.verdict == "STRONG_FIT"
        assert len(result.missing_skills) == 2

    def test_from_json(self):
        data = {
            "skills_match": 60,
            "domain_fit": 40,
            "experience_match": 70,
            "seniority_match": 55,
            "missing_skills": ["Python"],
            "verdict": "PARTIAL_FIT",
            "apply_priority": "REVIEW_FIRST",
            "reasoning": "Partial match.",
        }
        result = JobScoreResult.model_validate(data)
        assert result.skills_match == 60
        assert result.apply_priority == "REVIEW_FIRST"

    def test_json_roundtrip(self):
        result = JobScoreResult(
            skills_match=80,
            domain_fit=90,
            experience_match=75,
            seniority_match=85,
            missing_skills=["SQL"],
            verdict="GOOD_FIT",
            apply_priority="APPLY_NOW",
            reasoning="Good match.",
        )
        json_str = result.model_dump_json()
        parsed = JobScoreResult.model_validate_json(json_str)
        assert parsed.skills_match == result.skills_match
        assert parsed.verdict == result.verdict

    def test_schema_generation(self):
        schema = JobScoreResult.model_json_schema()
        assert "skills_match" in schema.get("properties", {})
        assert "verdict" in schema.get("properties", {})


# ==================================================================
# Tests: ScoringWeights
# ==================================================================

class TestScoringWeights:
    def test_defaults(self):
        w = ScoringWeights()
        assert w.skills_match == 0.30
        assert w.domain_fit == 0.25
        assert w.experience_match == 0.20
        assert w.seniority_match == 0.15
        assert w.recency == 0.10
        # Sum should be 1.0
        total = w.skills_match + w.domain_fit + w.experience_match + w.seniority_match + w.recency
        assert abs(total - 1.0) < 0.001

    def test_custom_weights(self):
        w = ScoringWeights(skills_match=0.5, domain_fit=0.2, experience_match=0.1,
                           seniority_match=0.1, recency=0.1)
        assert w.skills_match == 0.5


# ==================================================================
# Tests: GeminiScorer — configuration
# ==================================================================

class TestGeminiScorerConfig:
    def test_not_configured_empty_key(self):
        scorer = GeminiScorer(api_key="")
        assert not scorer.is_configured

    def test_not_configured_placeholder(self):
        scorer = GeminiScorer(api_key="your_gemini_api_key_here")
        assert not scorer.is_configured

    def test_configured(self):
        scorer = GeminiScorer(api_key="real_key_123")
        assert scorer.is_configured

    def test_default_model(self):
        scorer = GeminiScorer(api_key="key")
        from backend.config import GEMINI_MODEL
        assert scorer.model == GEMINI_MODEL


# ==================================================================
# Tests: GeminiScorer — score_job (mocked Gemini API)
# ==================================================================

class TestGeminiScorerScoring:
    def _mock_response(self) -> str:
        """Return a valid JSON response mimicking Gemini output."""
        return json.dumps({
            "skills_match": 75,
            "domain_fit": 85,
            "experience_match": 70,
            "seniority_match": 80,
            "missing_skills": ["Advanced SQL", "Tableau", "A/B Testing"],
            "verdict": "GOOD_FIT",
            "apply_priority": "APPLY_NOW",
            "reasoning": "Strong product skills. Good fintech domain alignment.",
        })

    @patch("backend.scoring.gemini_scorer.GeminiScorer._get_client")
    def test_score_job_success(self, mock_get_client):
        mock_response = MagicMock()
        mock_response.text = self._mock_response()
        mock_client = MagicMock()
        mock_client.models.generate_content.return_value = mock_response
        mock_get_client.return_value = mock_client

        # Patch the types module so GenerateContentConfig doesn't validate
        with patch("backend.scoring.gemini_scorer.types") as mock_types:
            mock_types.GenerateContentConfig.return_value = MagicMock()

            scorer = GeminiScorer(api_key="test_key", rpm_limit=100)
            result = scorer.score_job(
                resume_text="Experienced PM with 5 years in fintech...",
                job_title="Product Manager",
                company="Razorpay",
                location="Bangalore",
                job_description="We are looking for a PM to lead payments...",
            )

        assert result is not None
        assert result.skills_match == 75
        assert result.domain_fit == 85
        assert result.verdict == "GOOD_FIT"
        assert len(result.missing_skills) == 3

    def test_score_job_not_configured(self):
        scorer = GeminiScorer(api_key="")
        result = scorer.score_job(
            resume_text="test",
            job_title="PM",
            company="Test",
            location="City",
            job_description="desc",
        )
        assert result is None

    @patch("backend.scoring.gemini_scorer.GeminiScorer._get_client")
    def test_score_job_api_error(self, mock_get_client):
        mock_client = MagicMock()
        mock_client.models.generate_content.side_effect = Exception("API Error")
        mock_get_client.return_value = mock_client

        scorer = GeminiScorer(api_key="test_key", rpm_limit=100)
        result = scorer.score_job(
            resume_text="test",
            job_title="PM",
            company="Test",
            location="City",
            job_description="desc",
        )
        assert result is None


# ==================================================================
# Tests: GeminiScorer — compute_final_score
# ==================================================================

class TestComputeFinalScore:
    def test_basic_score(self):
        scorer = GeminiScorer(api_key="test")
        result = JobScoreResult(
            skills_match=80,
            domain_fit=90,
            experience_match=70,
            seniority_match=60,
            missing_skills=[],
            verdict="GOOD_FIT",
            apply_priority="APPLY_NOW",
            reasoning="Good fit.",
        )
        # 80*0.30 + 90*0.25 + 70*0.20 + 60*0.15 + 50*0.10 (default recency)
        # = 24 + 22.5 + 14 + 9 + 5 = 74.5
        score = scorer.compute_final_score(result, recency_score=50.0, domain_bonus=0.0)
        assert score == 74.5

    def test_with_domain_bonus(self):
        scorer = GeminiScorer(api_key="test")
        result = JobScoreResult(
            skills_match=80, domain_fit=90, experience_match=70,
            seniority_match=60, missing_skills=[], verdict="GOOD_FIT",
            apply_priority="APPLY_NOW", reasoning="Good fit.",
        )
        score = scorer.compute_final_score(result, recency_score=50.0, domain_bonus=15.0)
        # 74.5 + 15 = 89.5
        assert score == 89.5

    def test_capped_at_100(self):
        scorer = GeminiScorer(api_key="test")
        result = JobScoreResult(
            skills_match=100, domain_fit=100, experience_match=100,
            seniority_match=100, missing_skills=[], verdict="STRONG_FIT",
            apply_priority="APPLY_NOW", reasoning="Perfect fit.",
        )
        score = scorer.compute_final_score(result, recency_score=100.0, domain_bonus=15.0)
        assert score == 100.0  # Capped at 100

    def test_custom_weights(self):
        scorer = GeminiScorer(
            api_key="test",
            weights=ScoringWeights(
                skills_match=0.5, domain_fit=0.2, experience_match=0.1,
                seniority_match=0.1, recency=0.1,
            ),
        )
        result = JobScoreResult(
            skills_match=100, domain_fit=0, experience_match=0,
            seniority_match=0, missing_skills=[], verdict="PARTIAL_FIT",
            apply_priority="REVIEW_FIRST", reasoning="Only skills match.",
        )
        score = scorer.compute_final_score(result, recency_score=0.0, domain_bonus=0.0)
        # 100*0.5 + 0 + 0 + 0 + 0 = 50.0
        assert score == 50.0


# ==================================================================
# Tests: GeminiScorer — recency score
# ==================================================================

class TestRecencyScore:
    def test_very_fresh(self):
        """Jobs posted < 6 hours ago get 100."""
        assert GeminiScorer.compute_recency_score(0) == 100.0
        assert GeminiScorer.compute_recency_score(3) == 100.0
        assert GeminiScorer.compute_recency_score(6) == 100.0

    def test_same_day(self):
        """Jobs posted 6-24 hours ago decay from 100 to ~80."""
        score = GeminiScorer.compute_recency_score(15)
        assert 80.0 <= score <= 100.0

    def test_yesterday(self):
        """Jobs posted 24-48 hours ago decay from 80 to ~40."""
        score = GeminiScorer.compute_recency_score(36)
        assert 40.0 <= score <= 80.0

    def test_old_job(self):
        """Jobs posted > 48 hours ago decay slowly, floor at 20."""
        score = GeminiScorer.compute_recency_score(100)
        assert score >= 20.0

    def test_very_old_job(self):
        """Very old jobs get the floor score of 20."""
        score = GeminiScorer.compute_recency_score(500)
        assert score == 20.0

    def test_none_hours(self):
        """Unknown posting time returns 50."""
        assert GeminiScorer.compute_recency_score(None) == 50.0

    def test_negative_hours(self):
        """Negative hours (invalid) returns 50."""
        assert GeminiScorer.compute_recency_score(-5) == 50.0


# ==================================================================
# Tests: Rate limiting
# ==================================================================

class TestRateLimiting:
    def test_rate_limiter_cleans_old_timestamps(self):
        scorer = GeminiScorer(api_key="test", rpm_limit=15)
        # Add timestamps from 2 minutes ago (should be cleaned)
        old_time = time.time() - 120
        scorer._request_times = [old_time] * 20
        scorer._wait_for_rate_limit()
        # Old timestamps should be cleaned, only 1 new one
        assert len(scorer._request_times) == 1
