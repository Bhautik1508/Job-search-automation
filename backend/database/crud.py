"""
CRUD operations for the jobs database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy.orm import Session

from backend.database.models import Job, ScrapeScan


# ------------------------------------------------------------------
# Job CRUD
# ------------------------------------------------------------------

def get_all_jobs(session: Session, limit: int = 500, offset: int = 0) -> list[Job]:
    """Fetch all jobs ordered by date_scraped descending."""
    return (
        session.query(Job)
        .order_by(Job.date_scraped.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )


def get_job_by_id(session: Session, job_id: int) -> Job | None:
    """Fetch a single job by ID."""
    return session.query(Job).filter(Job.id == job_id).first()


def get_job_by_dedup_hash(session: Session, dedup_hash: str) -> Job | None:
    """Lookup a job by its deduplication hash."""
    return session.query(Job).filter(Job.dedup_hash == dedup_hash).first()


def insert_job(session: Session, job: Job) -> Job:
    """Insert a new job and commit."""
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def bulk_insert_jobs(session: Session, jobs: list[Job]) -> int:
    """Insert multiple jobs. Returns count of successfully inserted jobs."""
    inserted = 0
    for job in jobs:
        existing = get_job_by_dedup_hash(session, job.dedup_hash)
        if existing is None:
            session.add(job)
            inserted += 1
    session.commit()
    return inserted


def count_jobs(session: Session) -> int:
    """Count total jobs in DB."""
    return session.query(Job).count()


# ------------------------------------------------------------------
# Scoring CRUD (Phase 2)
# ------------------------------------------------------------------

def get_unscored_jobs(session: Session, limit: int = 1000) -> list[Job]:
    """Fetch jobs that haven't been scored yet."""
    return (
        session.query(Job)
        .filter(Job.relevancy_score.is_(None))
        .order_by(Job.date_scraped.desc())
        .limit(limit)
        .all()
    )


def update_job_scores(
    session: Session,
    job: Job,
    relevancy_score: float,
    skills_match_score: float,
    domain_fit_score: float,
    experience_match_score: float,
    seniority_match_score: float,
    recency_score: float,
    verdict: str,
    apply_priority: str,
    score_reasoning: str,
    missing_skills: str,
    company_type: str,
) -> Job:
    """Update a job's scoring fields and commit."""
    job.relevancy_score = relevancy_score
    job.skills_match_score = skills_match_score
    job.domain_fit_score = domain_fit_score
    job.experience_match_score = experience_match_score
    job.seniority_match_score = seniority_match_score
    job.recency_score = recency_score
    job.verdict = verdict
    job.apply_priority = apply_priority
    job.score_reasoning = score_reasoning
    job.missing_skills = missing_skills
    job.company_type = company_type
    job.date_scored = datetime.now(timezone.utc)
    session.commit()
    return job


def get_scored_jobs(
    session: Session,
    min_score: float | None = None,
    apply_priority: str | None = None,
    company_type: str | None = None,
    limit: int = 100,
) -> list[Job]:
    """Fetch scored jobs with optional filters."""
    query = session.query(Job).filter(Job.relevancy_score.isnot(None))

    if min_score is not None:
        query = query.filter(Job.relevancy_score >= min_score)
    if apply_priority:
        query = query.filter(Job.apply_priority == apply_priority)
    if company_type:
        query = query.filter(Job.company_type == company_type)

    return query.order_by(Job.relevancy_score.desc()).limit(limit).all()


# ------------------------------------------------------------------
# ScrapeScan CRUD
# ------------------------------------------------------------------

def create_scrape_scan(session: Session, **kwargs) -> ScrapeScan:
    """Create a new scrape scan record."""
    scan = ScrapeScan(**kwargs)
    session.add(scan)
    session.commit()
    session.refresh(scan)
    return scan


def complete_scrape_scan(
    session: Session,
    scan: ScrapeScan,
    jobs_found: int,
    jobs_new: int,
    jobs_duplicate: int,
    status: str = "completed",
    error_message: str | None = None,
):
    """Mark a scrape scan as completed."""
    scan.jobs_found = jobs_found
    scan.jobs_new = jobs_new
    scan.jobs_duplicate = jobs_duplicate
    scan.status = status
    scan.error_message = error_message
    scan.completed_at = datetime.now(timezone.utc)
    session.commit()


def get_recent_scans(session: Session, limit: int = 10) -> list[ScrapeScan]:
    """Fetch the most recent scrape scans."""
    return (
        session.query(ScrapeScan)
        .order_by(ScrapeScan.started_at.desc())
        .limit(limit)
        .all()
    )
