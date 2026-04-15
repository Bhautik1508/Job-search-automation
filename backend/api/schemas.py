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
