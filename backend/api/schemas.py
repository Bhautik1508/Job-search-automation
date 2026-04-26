"""
Pydantic response schemas for the REST API.
"""

from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


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

    # Application (R2)
    status: str = "new"
    application_status: str | None = None

    # Timestamps
    date_posted: datetime | None = None
    date_scraped: datetime | None = None
    date_scored: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


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

    model_config = ConfigDict(from_attributes=True)


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
    case_study_link: str | None = None
    case_study_attachment: str | None = None
    connection_id: int | None = None
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobOutreachResponse(BaseModel):
    """All outreach drafts for a job."""

    job_id: int
    drafts: list[OutreachDraftResponse] = []


class OutreachStatusUpdate(BaseModel):
    """
    Payload for PATCH /api/outreach/{id}.

    All fields optional — pass only what changed. Status moves the draft
    through draft → sent → replied; body/subject let the user edit copy
    in place before sending.
    """

    status: str | None = Field(
        default=None,
        description="draft | sent | replied",
    )
    body: str | None = Field(default=None, description="Edited message body.")
    subject: str | None = Field(default=None, description="Edited subject line.")


class JobStatusUpdate(BaseModel):
    """Payload for PATCH /api/jobs/{id} — change application status."""

    status: str = Field(
        description="new | saved | applied | interviewing | offer | rejected | hidden",
    )


# ------------------------------------------------------------------
# Connections (Phase R4)
# ------------------------------------------------------------------

class ConnectionResponse(BaseModel):
    """A single warm connection row."""

    id: int
    name: str
    company: str
    current_title: str | None = None
    linkedin_url: str | None = None
    source: str
    last_synced_at: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class JobConnectionsResponse(BaseModel):
    """Connections matched to a specific job by company."""

    job_id: int
    company: str
    connections: list[ConnectionResponse] = []


class ConnectionImportResponse(BaseModel):
    """Result of POST /api/connections/import."""

    imported: int
    updated: int
    skipped: int
    warnings: list[str] = []
    total_connections: int


class ReferralAskRequest(BaseModel):
    """Payload for POST /api/outreach/referral-ask."""

    job_id: int
    connection_id: int = Field(description="Warm peer who'll receive the DM.")
    target_contact_id: int = Field(
        description="The HM the user wants the warm peer to introduce them to.",
    )
    tone: str = Field(default="peer-pm", description="founder-pitch | peer-pm | recruiter-formal")


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

    by_verdict: list[VerdictCount] = []
    by_company_type: list[CompanyTypeCount] = []
    by_priority: list[PriorityCount] = []

    applied_count: int = 0
