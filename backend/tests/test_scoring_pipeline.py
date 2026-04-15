"""
Unit tests for the scoring pipeline module.

All external dependencies (Gemini API, PDF parsing) are mocked.
Uses an in-memory SQLite database for isolation.
"""

import json
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base, Job, init_db
from backend.database.crud import (
    insert_job, get_unscored_jobs, get_scored_jobs, update_job_scores,
)
from backend.scoring.gemini_scorer import GeminiScorer, JobScoreResult
from backend.scoring.company_classifier import CompanyClassifier
from backend.scoring.scoring_pipeline import ScoringPipeline


# ==================================================================
# Fixtures
# ==================================================================

@pytest.fixture
def db_session():
    """Create an in-memory SQLite database and session for testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


@pytest.fixture
def sample_jobs(db_session):
    """Insert sample jobs into the test database."""
    jobs = [
        Job(
            title="Product Manager",
            company="Razorpay",
            location="Bangalore",
            description="We are looking for a PM to lead our payments product...",
            source_portal="linkedin",
            source_engine="jobspy",
            date_posted=datetime.now(timezone.utc) - timedelta(hours=2),
            dedup_hash="hash_1",
        ),
        Job(
            title="Senior Product Manager",
            company="HDFC Bank",
            location="Mumbai",
            description="Join our digital banking team. Need 5+ years PM experience...",
            source_portal="naukri",
            source_engine="apify",
            date_posted=datetime.now(timezone.utc) - timedelta(hours=30),
            dedup_hash="hash_2",
        ),
        Job(
            title="Product Manager - Growth",
            company="Acme Corp",
            location="Delhi",
            description="Looking for a growth PM...",
            source_portal="indeed",
            source_engine="jobspy",
            dedup_hash="hash_3",
        ),
    ]
    for job in jobs:
        db_session.add(job)
    db_session.commit()
    return jobs


# ==================================================================
# Tests: CRUD — get_unscored_jobs
# ==================================================================

class TestUnscoredJobs:
    def test_all_unscored(self, db_session, sample_jobs):
        unscored = get_unscored_jobs(db_session)
        assert len(unscored) == 3

    def test_after_scoring_one(self, db_session, sample_jobs):
        job = sample_jobs[0]
        update_job_scores(
            db_session, job,
            relevancy_score=75.0,
            skills_match_score=80.0,
            domain_fit_score=90.0,
            experience_match_score=70.0,
            seniority_match_score=60.0,
            recency_score=100.0,
            verdict="GOOD_FIT",
            apply_priority="APPLY_NOW",
            score_reasoning="Strong match",
            missing_skills="SQL, Tableau",
            company_type="fintech",
        )
        unscored = get_unscored_jobs(db_session)
        assert len(unscored) == 2

    def test_respects_limit(self, db_session, sample_jobs):
        unscored = get_unscored_jobs(db_session, limit=2)
        assert len(unscored) == 2


# ==================================================================
# Tests: CRUD — update_job_scores
# ==================================================================

class TestUpdateJobScores:
    def test_updates_all_fields(self, db_session, sample_jobs):
        job = sample_jobs[0]
        updated = update_job_scores(
            db_session, job,
            relevancy_score=82.5,
            skills_match_score=80.0,
            domain_fit_score=90.0,
            experience_match_score=75.0,
            seniority_match_score=65.0,
            recency_score=100.0,
            verdict="STRONG_FIT",
            apply_priority="APPLY_NOW",
            score_reasoning="Excellent match with fintech PM role",
            missing_skills="Advanced SQL, Tableau",
            company_type="fintech",
        )
        assert updated.relevancy_score == 82.5
        assert updated.verdict == "STRONG_FIT"
        assert updated.company_type == "fintech"
        assert updated.date_scored is not None


# ==================================================================
# Tests: CRUD — get_scored_jobs
# ==================================================================

class TestGetScoredJobs:
    def _score_job(self, db_session, job, score, priority, company_type):
        update_job_scores(
            db_session, job,
            relevancy_score=score,
            skills_match_score=score,
            domain_fit_score=score,
            experience_match_score=score,
            seniority_match_score=score,
            recency_score=50.0,
            verdict="GOOD_FIT",
            apply_priority=priority,
            score_reasoning="test",
            missing_skills="",
            company_type=company_type,
        )

    def test_returns_scored_only(self, db_session, sample_jobs):
        self._score_job(db_session, sample_jobs[0], 80.0, "APPLY_NOW", "fintech")
        scored = get_scored_jobs(db_session)
        assert len(scored) == 1

    def test_filter_by_min_score(self, db_session, sample_jobs):
        self._score_job(db_session, sample_jobs[0], 80.0, "APPLY_NOW", "fintech")
        self._score_job(db_session, sample_jobs[1], 50.0, "SKIP", "bank")

        scored = get_scored_jobs(db_session, min_score=70.0)
        assert len(scored) == 1
        assert scored[0].relevancy_score == 80.0

    def test_filter_by_priority(self, db_session, sample_jobs):
        self._score_job(db_session, sample_jobs[0], 80.0, "APPLY_NOW", "fintech")
        self._score_job(db_session, sample_jobs[1], 50.0, "SKIP", "bank")

        apply_now = get_scored_jobs(db_session, apply_priority="APPLY_NOW")
        assert len(apply_now) == 1

    def test_filter_by_company_type(self, db_session, sample_jobs):
        self._score_job(db_session, sample_jobs[0], 80.0, "APPLY_NOW", "fintech")
        self._score_job(db_session, sample_jobs[1], 50.0, "SKIP", "bank")

        fintech_jobs = get_scored_jobs(db_session, company_type="fintech")
        assert len(fintech_jobs) == 1
        assert fintech_jobs[0].company == "Razorpay"

    def test_ordered_by_score_desc(self, db_session, sample_jobs):
        self._score_job(db_session, sample_jobs[0], 60.0, "REVIEW_FIRST", "fintech")
        self._score_job(db_session, sample_jobs[1], 90.0, "APPLY_NOW", "bank")

        scored = get_scored_jobs(db_session)
        assert scored[0].relevancy_score == 90.0
        assert scored[1].relevancy_score == 60.0


# ==================================================================
# Tests: ScoringPipeline
# ==================================================================

class TestScoringPipeline:
    def _make_scorer_mock(self):
        """Create a mock GeminiScorer that returns predetermined scores."""
        scorer = MagicMock(spec=GeminiScorer)
        scorer.is_configured = True
        scorer.score_job.return_value = JobScoreResult(
            skills_match=80,
            domain_fit=85,
            experience_match=70,
            seniority_match=75,
            missing_skills=["SQL", "Analytics"],
            verdict="GOOD_FIT",
            apply_priority="APPLY_NOW",
            reasoning="Strong PM with fintech alignment.",
        )
        scorer.compute_final_score.return_value = 82.5
        scorer.compute_recency_score = GeminiScorer.compute_recency_score
        return scorer

    def test_pipeline_is_ready(self):
        scorer = MagicMock(spec=GeminiScorer)
        scorer.is_configured = True
        pipeline = ScoringPipeline(
            resume_text="Experienced PM...",
            scorer=scorer,
            db_url="sqlite:///:memory:",
        )
        assert pipeline.is_ready

    def test_pipeline_not_ready_no_resume(self):
        scorer = MagicMock(spec=GeminiScorer)
        scorer.is_configured = True
        # Patch the default resume path to a nonexistent file
        with patch("backend.scoring.scoring_pipeline.BACKEND_DIR", __import__("pathlib").Path("/nonexistent")):
            pipeline = ScoringPipeline(
                scorer=scorer,
                db_url="sqlite:///:memory:",
            )
        assert not pipeline.is_ready

    def test_pipeline_not_ready_no_api_key(self):
        scorer = MagicMock(spec=GeminiScorer)
        scorer.is_configured = False
        pipeline = ScoringPipeline(
            resume_text="Some resume text",
            scorer=scorer,
            db_url="sqlite:///:memory:",
        )
        assert not pipeline.is_ready

    def test_pipeline_run_scores_jobs(self):
        """Full pipeline run with mocked Gemini and in-memory DB."""
        scorer = self._make_scorer_mock()
        classifier = CompanyClassifier()

        pipeline = ScoringPipeline(
            resume_text="Experienced PM with 5 years in fintech...",
            scorer=scorer,
            classifier=classifier,
            db_url="sqlite:///:memory:",
        )

        # Insert test jobs directly into the pipeline's DB
        session = pipeline._Session()
        job = Job(
            title="Product Manager",
            company="Razorpay",
            location="Bangalore",
            description="Lead payments product",
            source_portal="linkedin",
            source_engine="jobspy",
            date_posted=datetime.now(timezone.utc) - timedelta(hours=3),
            dedup_hash="test_hash_1",
        )
        session.add(job)
        session.commit()
        session.close()

        result = pipeline.run()

        assert result["scored"] == 1
        assert result["failed"] == 0
        assert result["total_unscored"] == 1

        # Verify the job was scored in the DB
        session = pipeline._Session()
        scored_jobs = get_scored_jobs(session)
        assert len(scored_jobs) == 1
        assert scored_jobs[0].relevancy_score == 82.5
        assert scored_jobs[0].company_type == "fintech"
        session.close()

    def test_pipeline_run_empty_db(self):
        scorer = self._make_scorer_mock()
        pipeline = ScoringPipeline(
            resume_text="PM resume...",
            scorer=scorer,
            db_url="sqlite:///:memory:",
        )
        result = pipeline.run()
        assert result["scored"] == 0
        assert result["total_unscored"] == 0

    def test_pipeline_run_not_ready(self):
        scorer = MagicMock(spec=GeminiScorer)
        scorer.is_configured = False
        pipeline = ScoringPipeline(
            resume_text=None,
            scorer=scorer,
            db_url="sqlite:///:memory:",
        )

        # Insert a test job
        session = pipeline._Session()
        session.add(Job(
            title="PM", company="Test", source_portal="linkedin",
            source_engine="jobspy", dedup_hash="h1",
        ))
        session.commit()
        session.close()

        result = pipeline.run()
        assert result["skipped"] == 1
        assert result["scored"] == 0

    def test_pipeline_handles_scoring_failure(self):
        scorer = MagicMock(spec=GeminiScorer)
        scorer.is_configured = True
        scorer.score_job.return_value = None  # Scoring fails
        scorer.compute_recency_score = GeminiScorer.compute_recency_score

        pipeline = ScoringPipeline(
            resume_text="PM resume...",
            scorer=scorer,
            db_url="sqlite:///:memory:",
        )

        session = pipeline._Session()
        session.add(Job(
            title="PM", company="Test", source_portal="linkedin",
            source_engine="jobspy", dedup_hash="h1",
        ))
        session.commit()
        session.close()

        result = pipeline.run()
        assert result["failed"] == 1
        assert result["scored"] == 0

    def test_pipeline_respects_limit(self):
        scorer = self._make_scorer_mock()
        pipeline = ScoringPipeline(
            resume_text="PM resume...",
            scorer=scorer,
            db_url="sqlite:///:memory:",
        )

        session = pipeline._Session()
        for i in range(5):
            session.add(Job(
                title=f"PM {i}", company="Test", source_portal="linkedin",
                source_engine="jobspy", dedup_hash=f"h_{i}",
            ))
        session.commit()
        session.close()

        result = pipeline.run(limit=2)
        assert result["total_unscored"] == 2
        assert result["scored"] == 2


# ==================================================================
# Tests: ScoringPipeline._hours_since_posted
# ==================================================================

class TestHoursSincePosted:
    def test_recent_post(self):
        posted = datetime.now(timezone.utc) - timedelta(hours=3)
        hours = ScoringPipeline._hours_since_posted(posted)
        assert 2.9 < hours < 3.1

    def test_none_posted(self):
        assert ScoringPipeline._hours_since_posted(None) is None

    def test_naive_datetime(self):
        """Naive datetimes (no timezone) are treated as UTC."""
        posted = datetime.utcnow() - timedelta(hours=5)
        hours = ScoringPipeline._hours_since_posted(posted)
        assert 4.9 < hours < 5.1
