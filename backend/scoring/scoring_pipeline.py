"""
Scoring pipeline — orchestrates the end-to-end flow of scoring jobs
from the database:

  1. Load unscored jobs from DB
  2. Parse resume text
  3. Classify each job's company (fintech/bank/nbfc/other)
  4. Score each job via Gemini
  5. Compute recency + domain bonuses
  6. Update DB with scores

Handles rate limiting and batch processing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from backend.database.models import Job, get_engine, get_session_factory, init_db
from backend.database.crud import get_unscored_jobs, update_job_scores
from backend.resume.parser import ResumeParser, load_resume_text
from backend.scoring.gemini_scorer import GeminiScorer, JobScoreResult, DailyQuotaExhausted
from backend.scoring.company_classifier import CompanyClassifier
from backend.config import BACKEND_DIR


class ScoringPipeline:
    """
    End-to-end scoring pipeline.

    Reads unscored jobs from the DB, scores them via Gemini, classifies
    their companies, and writes scores back.
    """

    def __init__(
        self,
        resume_path: str | Path | None = None,
        resume_text: str | None = None,
        scorer: GeminiScorer | None = None,
        classifier: CompanyClassifier | None = None,
        db_url: str | None = None,
        batch_size: int = 15,
    ):
        # Resume — uses a mtime-keyed cache so repeated pipeline runs skip re-parsing.
        if resume_text:
            self._resume_text = resume_text
        elif resume_path:
            self._resume_text = load_resume_text(resume_path)
        else:
            # Default: look for resume in backend/resume/
            default_path = BACKEND_DIR / "resume" / "resume.pdf"
            if default_path.exists():
                self._resume_text = load_resume_text(default_path)
            else:
                self._resume_text = None

        # Scorer
        self.scorer = scorer or GeminiScorer()
        self.classifier = classifier or CompanyClassifier()
        self.batch_size = batch_size

        # DB
        self._engine = get_engine(db_url)
        init_db(self._engine)
        self._Session = get_session_factory(self._engine)

    @property
    def resume_text(self) -> str | None:
        return self._resume_text

    @property
    def is_ready(self) -> bool:
        """Check if the pipeline has everything it needs."""
        return bool(self._resume_text) and self.scorer.is_configured

    def run(self, limit: int | None = None) -> dict:
        """
        Score all unscored jobs.

        Args:
            limit: Maximum number of jobs to score (None = all).

        Returns a summary dict:
            { scored, skipped, failed, total_unscored }
        """
        session = self._Session()

        try:
            # Step 1: Get unscored jobs
            unscored = get_unscored_jobs(session, limit=limit or 1000)
            total = len(unscored)
            print(f"\n📋 Found {total} unscored jobs")

            if not total:
                return {"scored": 0, "skipped": 0, "failed": 0, "total_unscored": 0}

            if not self.is_ready:
                reasons = []
                if not self._resume_text:
                    reasons.append("No resume text available")
                if not self.scorer.is_configured:
                    reasons.append("Gemini API key not configured")
                print(f"⚠️  Pipeline not ready: {', '.join(reasons)}")
                return {"scored": 0, "skipped": total, "failed": 0, "total_unscored": total,
                        "reason": ", ".join(reasons)}

            scored = 0
            skipped = 0
            failed = 0

            for i, job in enumerate(unscored):
                print(f"\n[{i + 1}/{total}] Scoring: {job.title} @ {job.company}")

                try:
                    # Classify company
                    company_type, confidence = self.classifier.classify(job.company or "")
                    domain_bonus = self.classifier.get_domain_bonus(company_type)

                    if company_type != "other":
                        print(f"   🏦 Company type: {company_type} (confidence: {confidence:.2f})")

                    # Compute recency score
                    hours_since = self._hours_since_posted(job.date_posted)
                    recency = GeminiScorer.compute_recency_score(hours_since)

                    # Score with Gemini
                    result = self.scorer.score_job(
                        resume_text=self._resume_text,
                        job_title=job.title,
                        company=job.company,
                        location=job.location,
                        job_description=job.description,
                    )

                    if result is None:
                        print(f"   ❌ Scoring failed — skipping")
                        failed += 1
                        continue

                    # Compute final weighted score
                    final_score = self.scorer.compute_final_score(
                        result, recency_score=recency, domain_bonus=domain_bonus,
                    )

                    # Update DB
                    update_job_scores(
                        session,
                        job,
                        relevancy_score=final_score,
                        skills_match_score=float(result.skills_match),
                        domain_fit_score=float(result.domain_fit),
                        experience_match_score=float(result.experience_match),
                        seniority_match_score=float(result.seniority_match),
                        recency_score=recency,
                        verdict=result.verdict,
                        apply_priority=result.apply_priority,
                        score_reasoning=result.reasoning,
                        missing_skills=", ".join(result.missing_skills),
                        company_type=company_type,
                    )

                    print(f"   ✅ Score: {final_score} | {result.verdict} | {result.apply_priority}")
                    scored += 1

                except DailyQuotaExhausted as e:
                    print(f"\n🛑 {e}")
                    print(f"   Stopping scoring. {scored} jobs scored so far.")
                    failed += (total - i)
                    break

                except Exception as e:
                    print(f"   ❌ Error: {e}")
                    failed += 1

            summary = {
                "scored": scored,
                "skipped": skipped,
                "failed": failed,
                "total_unscored": total,
            }
            print(f"\n📊 Scoring Summary: {summary}")
            return summary

        finally:
            session.close()

    @staticmethod
    def _hours_since_posted(date_posted: datetime | None) -> float | None:
        """Calculate hours since the job was posted."""
        if date_posted is None:
            return None
        now = datetime.now(timezone.utc)
        # Ensure date_posted is timezone-aware
        if date_posted.tzinfo is None:
            date_posted = date_posted.replace(tzinfo=timezone.utc)
        delta = now - date_posted
        return delta.total_seconds() / 3600.0
