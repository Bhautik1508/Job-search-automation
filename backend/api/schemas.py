"""
Pydantic response schemas for the REST API.
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field


class JobResponse(BaseModel):
    """Single job in API responses."""

    id: int
    title: str
    company: str
    location: str | None = None
    description: str | None = None
    job_url: str | None = None

    # Source
    source_portal: str
    source_engine: str

    # Details
    salary_min: float | None = None
    salary_max: float | None = None
    salary_currency: str | None = None
    experience_required: str | None = None
    skills: str | None = None
    job_type: str | None = None
    work_mode: str | None = None

    # Classification
    company_type: str | None = None
    company_tier: str | None = None
    funding_stage: str | None = None
    headcount_band: str | None = None

    # Scoring
    relevancy_score: float | None = None
    skills_match_score: float | None = None
    domain_fit_score: float | None = None
    experience_match_score: float | None = None
    seniority_match_score: float | None = None
    recency_score: float | None = None
    verdict: str | None = None
    apply_priority: str | None = None
    score_reasoning: str | None = None
    missing_skills: str | None = None

    # Application
    applied: bool = False
    application_status: str | None = None

    # Timestamps
    date_posted: datetime | None = None
    date_scraped: datetime | None = None
    date_scored: datetime | None = None

    class Config:
        from_attributes = True


class JobListResponse(BaseModel):
    """Paginated list of jobs."""

    jobs: list[JobResponse]
    total: int
    page: int
    page_size: int
    total_pages: int


class VerdictCount(BaseModel):
    verdict: str
    count: int


class CompanyTypeCount(BaseModel):
    company_type: str
    count: int


class PriorityCount(BaseModel):
    priority: str
    count: int


class CompanyTierCount(BaseModel):
    tier: str
    count: int


class CareersLink(BaseModel):
    name: str
    tier: str
    careers_url: str


class SchedulerStatusResponse(BaseModel):
    """Liveness indicator for the background scheduler worker."""

    last_scrape_at: datetime | None = None
    last_scrape_status: str | None = None
    last_scrape_new_jobs: int | None = None
    last_score_at: datetime | None = None
    scored_jobs_last_24h: int = 0


class ContactResponse(BaseModel):
    """A single contact linked to (or available for) a job."""

    id: int
    name: str
    title: str | None = None
    company: str
    linkedin_url: str | None = None
    email: str | None = None
    role_type: str
    confidence: float | None = None
    source_provider: str
    link_provider: str | None = None
    link_confidence: float | None = None
    last_enriched_at: datetime | None = None

    class Config:
        from_attributes = True


class JobContactsResponse(BaseModel):
    """Contacts linked to a specific job."""

    job_id: int
    company: str
    contacts: list[ContactResponse] = []


class EnrichmentResponse(BaseModel):
    """Result summary for POST /api/enrich-contacts."""

    status: str
    jobs_considered: int = 0
    jobs_eligible: int = 0
    jobs_enriched: int = 0
    jobs_skipped: int = 0
    contacts_created: int = 0
    contacts_reused_from_cache: int = 0
    links_created: int = 0
    skip_reasons: dict[str, int] = {}
    provider_errors: list[str] = []


class OutreachDraftRequest(BaseModel):
    """Payload for POST /api/outreach/draft — generate a new draft."""

    job_id: int
    contact_id: int
    channel: str = Field(description="linkedin_note | linkedin_inmail | email | referral_ask")
    tone: str = Field(description="founder-pitch | peer-pm | recruiter-formal")


class OutreachDraftResponse(BaseModel):
    """An outreach draft row."""

    id: int
    job_id: int
    contact_id: int
    channel: str
    tone: str
    subject: str | None = None
    body: str
    attachments: str | None = None
    status: str
    model: str | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class JobOutreachResponse(BaseModel):
    """All outreach drafts for a job."""

    job_id: int
    drafts: list[OutreachDraftResponse] = []


class OutreachStatusUpdate(BaseModel):
    """Payload for PATCH /api/outreach/{id} — change status."""

    status: str = Field(description="draft | sent | replied")


class StatsResponse(BaseModel):
    """Dashboard KPI summary."""

    total_jobs: int = 0
    scored_jobs: int = 0
    unscored_jobs: int = 0
    avg_score: float = 0.0
    max_score: float = 0.0
    min_score: float = 0.0

    apply_now_count: int = 0
    review_first_count: int = 0
    skip_count: int = 0

    fintech_count: int = 0
    bank_count: int = 0
    nbfc_count: int = 0
    other_count: int = 0

    top_tier_count: int = 0
    unicorn_count: int = 0
    growth_startup_count: int = 0
    early_startup_count: int = 0

    by_verdict: list[VerdictCount] = []
    by_company_type: list[CompanyTypeCount] = []
    by_priority: list[PriorityCount] = []
    by_company_tier: list[CompanyTierCount] = []

    applied_count: int = 0
