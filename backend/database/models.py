"""
SQLAlchemy models for the Job Search Automation database.
"""

from __future__ import annotations

from datetime import datetime, timezone
from sqlalchemy import (
    Column,
    Integer,
    String,
    Text,
    Float,
    DateTime,
    Boolean,
    Index,
    create_engine,
)
from sqlalchemy.orm import declarative_base, sessionmaker

from backend.config import DATABASE_URL

Base = declarative_base()


class Job(Base):
    """A single scraped job listing."""

    __tablename__ = "jobs"

    id = Column(Integer, primary_key=True, autoincrement=True)

    # ---- Core fields ----
    title = Column(String(500), nullable=False)
    company = Column(String(300), nullable=False)
    location = Column(String(300), nullable=True)
    description = Column(Text, nullable=True)
    job_url = Column(String(2000), nullable=True)

    # ---- Source metadata ----
    source_portal = Column(String(50), nullable=False)   # naukri, linkedin, indeed, google, glassdoor
    source_engine = Column(String(20), nullable=False)    # jobspy, apify, instahyre
    external_id = Column(String(500), nullable=True)      # ID from the portal (for dedup)

    # ---- Job details ----
    salary_min = Column(Float, nullable=True)
    salary_max = Column(Float, nullable=True)
    salary_currency = Column(String(10), nullable=True)
    experience_required = Column(String(100), nullable=True)
    skills = Column(Text, nullable=True)                  # comma-separated
    job_type = Column(String(50), nullable=True)          # full-time, contract, etc.
    work_mode = Column(String(50), nullable=True)         # remote, hybrid, onsite

    # ---- Classification ----
    company_type = Column(String(30), nullable=True)      # fintech, bank, nbfc, digital_banking_arm, other

    # ---- Scoring (Phase 2 — populated later) ----
    relevancy_score = Column(Float, nullable=True)
    skills_match_score = Column(Float, nullable=True)
    domain_fit_score = Column(Float, nullable=True)
    experience_match_score = Column(Float, nullable=True)
    seniority_match_score = Column(Float, nullable=True)
    recency_score = Column(Float, nullable=True)
    verdict = Column(String(20), nullable=True)           # STRONG_FIT, GOOD_FIT, etc.
    apply_priority = Column(String(20), nullable=True)    # APPLY_NOW, REVIEW_FIRST, SKIP
    score_reasoning = Column(Text, nullable=True)
    missing_skills = Column(Text, nullable=True)

    # ---- Application tracking (Phase 4) ----
    applied = Column(Boolean, default=False)
    application_status = Column(String(30), nullable=True)  # applied, interviewing, rejected, offer

    # ---- Timestamps ----
    date_posted = Column(DateTime, nullable=True)
    date_scraped = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    date_scored = Column(DateTime, nullable=True)

    # ---- Deduplication ----
    dedup_hash = Column(String(64), nullable=True, index=True, unique=True)

    # Hot-path indexes for /api/jobs filters and /api/stats GROUP BYs.
    # Kept in sync with Alembic revision 0002_indexes so init_db() on a
    # fresh DB matches `alembic upgrade head`.
    __table_args__ = (
        Index("ix_jobs_relevancy_score", "relevancy_score"),
        Index("ix_jobs_apply_priority", "apply_priority"),
        Index("ix_jobs_company_type", "company_type"),
        Index("ix_jobs_verdict", "verdict"),
        Index("ix_jobs_date_scraped", "date_scraped"),
        Index("ix_jobs_applied_relevancy", "applied", "relevancy_score"),
    )

    def __repr__(self):
        return f"<Job(id={self.id}, title='{self.title}', company='{self.company}', source='{self.source_portal}')>"


class ScrapeScan(Base):
    """Metadata about a single scrape run."""

    __tablename__ = "scrape_scans"

    id = Column(Integer, primary_key=True, autoincrement=True)
    engine = Column(String(20), nullable=False)        # jobspy, apify, orchestrator
    portals = Column(String(200), nullable=True)       # comma-separated portal names
    search_term = Column(String(200), nullable=True)
    location = Column(String(200), nullable=True)
    jobs_found = Column(Integer, default=0)
    jobs_new = Column(Integer, default=0)               # after dedup
    jobs_duplicate = Column(Integer, default=0)
    status = Column(String(20), default="running")      # running, completed, failed
    error_message = Column(Text, nullable=True)
    started_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    def __repr__(self):
        return f"<ScrapeScan(id={self.id}, engine='{self.engine}', status='{self.status}', jobs_new={self.jobs_new})>"


# ------------------------------------------------------------------
# Engine & Session factory
# ------------------------------------------------------------------

def get_engine(url: str | None = None):
    """
    Create a SQLAlchemy engine.

    Adds `pool_pre_ping=True` for non-SQLite URLs so Postgres/MySQL
    connections that have been closed by the server (e.g. idle-timeout
    on managed DBs) are detected and recycled before use instead of
    surfacing as mid-request errors.
    """
    target = url or DATABASE_URL
    kwargs: dict = {"echo": False}
    if not target.startswith("sqlite"):
        kwargs["pool_pre_ping"] = True
    return create_engine(target, **kwargs)


def get_session_factory(engine=None):
    """Return a sessionmaker bound to the given engine."""
    if engine is None:
        engine = get_engine()
    return sessionmaker(bind=engine)


def init_db(engine=None):
    """Create all tables if they don't exist."""
    if engine is None:
        engine = get_engine()
    Base.metadata.create_all(engine)
    return engine
