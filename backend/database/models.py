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
    ForeignKey,
    Index,
    UniqueConstraint,
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
    company_tier = Column(String(30), nullable=True)      # top_tier, unicorn, growth_startup, early_startup, other
    funding_stage = Column(String(30), nullable=True)     # seed, series_a..f, pre_ipo, public, bootstrapped, unknown
    headcount_band = Column(String(30), nullable=True)    # <50, 50-200, 200-1000, 1000-5000, 5000+

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

    # ---- Application tracking ----
    # Phase R2 status enum: new | saved | applied | interviewing | offer | rejected | hidden.
    # `hidden` is soft-delete; default UI filter excludes hidden + rejected.
    status = Column(String(20), nullable=False, default="new", server_default="new")
    # Legacy bool — shadow column kept in sync by the API for one release; R5 drops it.
    applied = Column(Boolean, default=False)
    application_status = Column(String(30), nullable=True)

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
        Index("ix_jobs_company_tier", "company_tier"),
        Index("ix_jobs_status", "status"),
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


class Contact(Base):
    """
    A discovered hiring-manager, recruiter, or referral contact.

    Deduplicated on linkedin_url when present — the enrichment pipeline
    upserts by URL so re-running against the same company is cheap.
    """

    __tablename__ = "contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)

    name = Column(String(200), nullable=False)
    title = Column(String(300), nullable=True)
    company = Column(String(300), nullable=False)
    linkedin_url = Column(String(500), nullable=True, unique=True)
    email = Column(String(300), nullable=True)

    role_type = Column(String(20), nullable=False)          # hm | recruiter | referral
    confidence = Column(Float, nullable=True)
    source_provider = Column(String(30), nullable=False)    # apollo | hunter | linkedin_apify | manual
    raw_payload = Column(Text, nullable=True)               # raw JSON for debugging

    last_enriched_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        Index("ix_contacts_company", "company"),
        Index("ix_contacts_company_role", "company", "role_type"),
        Index("ix_contacts_last_enriched_at", "last_enriched_at"),
    )

    def __repr__(self):
        return (
            f"<Contact(id={self.id}, name='{self.name}', company='{self.company}', "
            f"role_type='{self.role_type}')>"
        )


class OutreachDraft(Base):
    """
    A generated outreach message targeting a specific (job, contact) pair.

    One row per (job_id, contact_id, channel) — regenerating replaces the
    body/subject in-place so we don't accumulate variants.
    """

    __tablename__ = "outreach_drafts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)

    channel = Column(String(30), nullable=False)   # linkedin_note | linkedin_inmail | email | referral_ask
    tone = Column(String(30), nullable=False)      # founder-pitch | peer-pm | recruiter-formal
    subject = Column(String(500), nullable=True)
    body = Column(Text, nullable=False)
    attachments = Column(Text, nullable=True)      # JSON list of portfolio item IDs

    status = Column(String(20), nullable=False, default="draft")  # draft | sent | replied
    model = Column(String(60), nullable=True)      # gemini model id that produced this draft

    # Phase R3 case-study attach — derived from the portfolio item the
    # generator referenced. Either/both may be null.
    case_study_link = Column(String(500), nullable=True)
    case_study_attachment = Column(String(500), nullable=True)

    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint(
            "job_id", "contact_id", "channel",
            name="uq_outreach_drafts_job_contact_channel",
        ),
        Index("ix_outreach_drafts_job_id", "job_id"),
        Index("ix_outreach_drafts_contact_id", "contact_id"),
        Index("ix_outreach_drafts_status", "status"),
    )

    def __repr__(self):
        return (
            f"<OutreachDraft(id={self.id}, job_id={self.job_id}, "
            f"contact_id={self.contact_id}, channel='{self.channel}', status='{self.status}')>"
        )


class JobContact(Base):
    """
    Link between a Job and a Contact — a contact can be relevant to
    many jobs (company-level), so we don't duplicate the contact row.
    """

    __tablename__ = "job_contacts"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False)
    contact_id = Column(Integer, ForeignKey("contacts.id", ondelete="CASCADE"), nullable=False)
    provider = Column(String(30), nullable=False)
    confidence = Column(Float, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc), nullable=False)

    __table_args__ = (
        UniqueConstraint("job_id", "contact_id", name="uq_job_contacts_pair"),
        Index("ix_job_contacts_job_id", "job_id"),
        Index("ix_job_contacts_contact_id", "contact_id"),
    )

    def __repr__(self):
        return f"<JobContact(job_id={self.job_id}, contact_id={self.contact_id})>"


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
