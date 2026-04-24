"""
CRUD operations for the jobs database.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database.models import Contact, Job, JobContact, OutreachDraft, ScrapeScan


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
    """
    Insert multiple jobs, skipping any whose dedup_hash already exists.
    Returns the count of newly inserted jobs.

    Uses a single batched `IN` query to find existing hashes, avoiding
    an N+1 lookup over the jobs list.
    """
    if not jobs:
        return 0

    # Dedupe input by hash first — protects against duplicates within the batch.
    seen: set[str] = set()
    candidates: list[Job] = []
    for job in jobs:
        if job.dedup_hash and job.dedup_hash not in seen:
            seen.add(job.dedup_hash)
            candidates.append(job)

    # One query to find which hashes are already in the DB.
    existing_hashes = {
        row[0]
        for row in session.query(Job.dedup_hash)
        .filter(Job.dedup_hash.in_(list(seen)))
        .all()
    }

    to_insert = [j for j in candidates if j.dedup_hash not in existing_hashes]
    if to_insert:
        session.bulk_save_objects(to_insert)
        session.commit()
    return len(to_insert)


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
    company_tier: str | None = None,
    funding_stage: str | None = None,
    headcount_band: str | None = None,
) -> Job:
    """Update a job's scoring fields and commit.

    Phase 6 adds optional `company_tier` / `funding_stage` / `headcount_band`.
    Kept optional to keep the pre-Phase-6 contract stable for tests and any
    other callers that only compute the domain classification.
    """
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
    if company_tier is not None:
        job.company_tier = company_tier
    if funding_stage is not None:
        job.funding_stage = funding_stage
    if headcount_band is not None:
        job.headcount_band = headcount_band
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


# ------------------------------------------------------------------
# Contact CRUD (Phase 7)
# ------------------------------------------------------------------

def upsert_contact(
    session: Session,
    *,
    name: str,
    company: str,
    role_type: str,
    source_provider: str,
    title: str | None = None,
    linkedin_url: str | None = None,
    email: str | None = None,
    confidence: float | None = None,
    raw_payload: str | None = None,
) -> Contact:
    """
    Insert or update a contact, deduplicating on linkedin_url when present.

    Without a linkedin_url we fall back to (company, name) — less reliable
    but prevents exact-name duplicates for providers (e.g. Hunter) that
    return emails without LinkedIn URLs.

    On update, refreshes last_enriched_at so the 30-day cache TTL resets.
    """
    existing: Contact | None = None
    if linkedin_url:
        existing = (
            session.query(Contact).filter(Contact.linkedin_url == linkedin_url).first()
        )
    else:
        existing = (
            session.query(Contact)
            .filter(Contact.company == company, Contact.name == name)
            .first()
        )

    now = datetime.now(timezone.utc)

    if existing:
        existing.name = name
        existing.title = title or existing.title
        existing.company = company
        existing.email = email or existing.email
        existing.role_type = role_type
        existing.confidence = confidence if confidence is not None else existing.confidence
        existing.source_provider = source_provider
        existing.raw_payload = raw_payload or existing.raw_payload
        existing.last_enriched_at = now
        session.commit()
        session.refresh(existing)
        return existing

    contact = Contact(
        name=name,
        title=title,
        company=company,
        linkedin_url=linkedin_url,
        email=email,
        role_type=role_type,
        confidence=confidence,
        source_provider=source_provider,
        raw_payload=raw_payload,
        last_enriched_at=now,
        created_at=now,
    )
    session.add(contact)
    session.commit()
    session.refresh(contact)
    return contact


def link_job_to_contact(
    session: Session,
    *,
    job_id: int,
    contact_id: int,
    provider: str,
    confidence: float | None = None,
) -> JobContact:
    """
    Create a job↔contact link, idempotent on (job_id, contact_id).
    Updates provider/confidence if the link already exists so re-enrichment
    from a higher-confidence source can overwrite a weaker match.
    """
    existing = (
        session.query(JobContact)
        .filter(JobContact.job_id == job_id, JobContact.contact_id == contact_id)
        .first()
    )
    if existing:
        existing.provider = provider
        if confidence is not None:
            existing.confidence = confidence
        session.commit()
        return existing

    link = JobContact(
        job_id=job_id,
        contact_id=contact_id,
        provider=provider,
        confidence=confidence,
        created_at=datetime.now(timezone.utc),
    )
    session.add(link)
    session.commit()
    session.refresh(link)
    return link


def get_contacts_for_company(
    session: Session,
    company: str,
    *,
    role_type: str | None = None,
    max_age_days: int | None = None,
) -> list[Contact]:
    """
    Fetch contacts at a company, optionally filtered by role_type and
    freshness (last_enriched_at within the last N days).

    Company matching is case-insensitive — Apollo's name normalization
    rarely matches a scraper's verbatim company string exactly.
    """
    query = session.query(Contact).filter(func.lower(Contact.company) == company.lower())
    if role_type:
        query = query.filter(Contact.role_type == role_type)
    if max_age_days is not None:
        cutoff = datetime.now(timezone.utc) - timedelta(days=max_age_days)
        query = query.filter(Contact.last_enriched_at >= cutoff)
    return query.order_by(Contact.confidence.desc().nullslast()).all()


def get_contacts_for_job(session: Session, job_id: int) -> list[tuple[Contact, JobContact]]:
    """
    Return (Contact, JobContact) pairs linked to a job, ranked by
    link confidence descending (falls back to Contact.confidence).
    """
    rows = (
        session.query(Contact, JobContact)
        .join(JobContact, Contact.id == JobContact.contact_id)
        .filter(JobContact.job_id == job_id)
        .order_by(
            JobContact.confidence.desc().nullslast(),
            Contact.confidence.desc().nullslast(),
        )
        .all()
    )
    return rows


def count_recent_enrichments(session: Session, *, within_hours: int = 24) -> int:
    """
    Count contacts enriched in the last N hours — used by the daily cap
    guardrail to avoid runaway spend.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=within_hours)
    return session.query(Contact).filter(Contact.last_enriched_at >= cutoff).count()


# ------------------------------------------------------------------
# OutreachDraft CRUD (Phase 8)
# ------------------------------------------------------------------

def upsert_outreach_draft(
    session: Session,
    *,
    job_id: int,
    contact_id: int,
    channel: str,
    tone: str,
    body: str,
    subject: str | None = None,
    attachments: str | None = None,
    model: str | None = None,
    status: str | None = None,
) -> OutreachDraft:
    """
    Insert-or-update an outreach draft keyed on (job_id, contact_id, channel).

    Regenerating replaces body/subject/tone in-place rather than accumulating
    variants — the uniqueness constraint on the table enforces this at the DB
    level. `status` is only overwritten when the caller passes it explicitly
    so we don't reset a "sent" draft back to "draft" when a user edits copy.
    """
    existing = (
        session.query(OutreachDraft)
        .filter(
            OutreachDraft.job_id == job_id,
            OutreachDraft.contact_id == contact_id,
            OutreachDraft.channel == channel,
        )
        .first()
    )

    now = datetime.now(timezone.utc)

    if existing:
        existing.tone = tone
        existing.body = body
        existing.subject = subject
        existing.attachments = attachments
        existing.model = model or existing.model
        if status is not None:
            existing.status = status
        existing.updated_at = now
        session.commit()
        session.refresh(existing)
        return existing

    draft = OutreachDraft(
        job_id=job_id,
        contact_id=contact_id,
        channel=channel,
        tone=tone,
        subject=subject,
        body=body,
        attachments=attachments,
        model=model,
        status=status or "draft",
        created_at=now,
        updated_at=now,
    )
    session.add(draft)
    session.commit()
    session.refresh(draft)
    return draft


def get_outreach_drafts_for_job(session: Session, job_id: int) -> list[OutreachDraft]:
    """Fetch all outreach drafts for a job, newest-updated first."""
    return (
        session.query(OutreachDraft)
        .filter(OutreachDraft.job_id == job_id)
        .order_by(OutreachDraft.updated_at.desc())
        .all()
    )


def get_outreach_draft_by_id(session: Session, draft_id: int) -> OutreachDraft | None:
    """Fetch a single outreach draft by ID."""
    return session.query(OutreachDraft).filter(OutreachDraft.id == draft_id).first()


def update_outreach_status(
    session: Session,
    draft_id: int,
    status: str,
) -> OutreachDraft | None:
    """Update draft status (draft → sent → replied) and bump updated_at."""
    draft = get_outreach_draft_by_id(session, draft_id)
    if draft is None:
        return None
    draft.status = status
    draft.updated_at = datetime.now(timezone.utc)
    session.commit()
    session.refresh(draft)
    return draft
