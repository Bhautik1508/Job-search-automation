"""
FastAPI application — REST API for the Job Search Automation dashboard.

Endpoints:
    GET   /api/jobs              — Paginated, filterable job list
    GET   /api/jobs/{id}         — Single job detail
    GET   /api/stats             — Dashboard KPI summary
    PATCH /api/jobs/{id}/applied — Toggle applied status
    POST  /api/scrape            — Trigger a scrape cycle
    POST  /api/score             — Trigger scoring of unscored jobs
    GET   /api/actions/status    — Status of running background actions
    GET   /api/health            — Health check
"""

from __future__ import annotations

import math
import os
import threading
import traceback
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.database.models import Job, get_engine, get_session_factory, init_db
from backend.api.schemas import (
    JobResponse,
    JobListResponse,
    StatsResponse,
    VerdictCount,
    CompanyTypeCount,
    PriorityCount,
)

# ------------------------------------------------------------------
# App setup
# ------------------------------------------------------------------

app = FastAPI(
    title="Job Search Automation API",
    version="1.0.0",
    description="REST API for the Job Search Automation dashboard",
)

# CORS — allow frontend (local dev + Vercel production)
_cors_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]
# Add production frontend URL if set
_frontend_url = os.getenv("FRONTEND_URL", "")
if _frontend_url:
    _cors_origins.append(_frontend_url)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_origin_regex=r"https://.*\.vercel\.app",  # All Vercel preview deploys
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# DB — lazy init
_engine = None
_SessionFactory = None


def _get_session() -> Session:
    """Get a DB session, initialising engine on first call."""
    global _engine, _SessionFactory
    if _engine is None:
        _engine = get_engine()
        init_db(_engine)
        _SessionFactory = get_session_factory(_engine)
    return _SessionFactory()


# ------------------------------------------------------------------
# Background action state
# ------------------------------------------------------------------

_action_state = {
    "scrape": {"running": False, "last_result": None, "started_at": None, "error": None},
    "score":  {"running": False, "last_result": None, "started_at": None, "error": None},
}
_action_lock = threading.Lock()


# ------------------------------------------------------------------
# Health
# ------------------------------------------------------------------

@app.get("/api/health")
def health_check():
    return {"status": "ok", "service": "job-search-automation"}


# ------------------------------------------------------------------
# Jobs
# ------------------------------------------------------------------

@app.get("/api/jobs", response_model=JobListResponse)
def list_jobs(
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    min_score: float | None = Query(None, ge=0, le=100),
    max_score: float | None = Query(None, ge=0, le=100),
    priority: str | None = Query(None),
    company_type: str | None = Query(None),
    verdict: str | None = Query(None),
    search: str | None = Query(None),
    sort_by: str = Query("relevancy_score"),
    sort_dir: str = Query("desc"),
    scored_only: bool = Query(False),
):
    """List jobs with pagination, filtering, sorting, and search."""
    session = _get_session()
    try:
        query = session.query(Job)

        # Filters
        if scored_only:
            query = query.filter(Job.relevancy_score.isnot(None))
        if min_score is not None:
            query = query.filter(Job.relevancy_score >= min_score)
        if max_score is not None:
            query = query.filter(Job.relevancy_score <= max_score)
        if priority:
            query = query.filter(Job.apply_priority == priority)
        if company_type:
            query = query.filter(Job.company_type == company_type)
        if verdict:
            query = query.filter(Job.verdict == verdict)
        if search:
            search_term = f"%{search}%"
            query = query.filter(
                (Job.title.ilike(search_term)) | (Job.company.ilike(search_term))
            )

        # Count
        total = query.count()
        total_pages = max(1, math.ceil(total / page_size))

        # Sort
        sort_column = _get_sort_column(sort_by)
        if sort_dir.lower() == "asc":
            query = query.order_by(sort_column.asc().nullslast())
        else:
            query = query.order_by(sort_column.desc().nullslast())

        # Paginate
        offset = (page - 1) * page_size
        jobs = query.offset(offset).limit(page_size).all()

        return JobListResponse(
            jobs=[JobResponse.model_validate(j) for j in jobs],
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
        )
    finally:
        session.close()


@app.get("/api/jobs/{job_id}", response_model=JobResponse)
def get_job(job_id: int):
    """Get a single job by ID."""
    session = _get_session()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return JobResponse.model_validate(job)
    finally:
        session.close()


@app.patch("/api/jobs/{job_id}/applied")
def toggle_applied(job_id: int, applied: bool = Query(True)):
    """Mark or unmark a job as applied."""
    session = _get_session()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        job.applied = applied
        job.application_status = "applied" if applied else None
        session.commit()
        return {"id": job_id, "applied": applied}
    finally:
        session.close()


# ------------------------------------------------------------------
# Stats
# ------------------------------------------------------------------

@app.get("/api/stats", response_model=StatsResponse)
def get_stats():
    """Dashboard KPI summary."""
    session = _get_session()
    try:
        total_jobs = session.query(Job).count()
        scored_jobs = session.query(Job).filter(Job.relevancy_score.isnot(None)).count()
        unscored_jobs = total_jobs - scored_jobs

        # Score aggregates
        score_agg = session.query(
            func.avg(Job.relevancy_score),
            func.max(Job.relevancy_score),
            func.min(Job.relevancy_score),
        ).filter(Job.relevancy_score.isnot(None)).first()

        avg_score = round(score_agg[0] or 0, 1)
        max_score = round(score_agg[1] or 0, 1)
        min_score = round(score_agg[2] or 0, 1)

        # Priority counts
        apply_now = session.query(Job).filter(Job.apply_priority == "APPLY_NOW").count()
        review_first = session.query(Job).filter(Job.apply_priority == "REVIEW_FIRST").count()
        skip = session.query(Job).filter(Job.apply_priority == "SKIP").count()

        # Company type counts
        fintech = session.query(Job).filter(Job.company_type == "fintech").count()
        bank = session.query(Job).filter(Job.company_type == "bank").count()
        nbfc = session.query(Job).filter(Job.company_type == "nbfc").count()
        other = session.query(Job).filter(Job.company_type == "other").count()

        # By-verdict breakdown
        verdict_rows = (
            session.query(Job.verdict, func.count(Job.id))
            .filter(Job.verdict.isnot(None))
            .group_by(Job.verdict)
            .all()
        )
        by_verdict = [VerdictCount(verdict=v, count=c) for v, c in verdict_rows]

        # By-company-type breakdown
        ctype_rows = (
            session.query(Job.company_type, func.count(Job.id))
            .filter(Job.company_type.isnot(None))
            .group_by(Job.company_type)
            .all()
        )
        by_company_type = [CompanyTypeCount(company_type=ct, count=c) for ct, c in ctype_rows]

        # By-priority breakdown
        prio_rows = (
            session.query(Job.apply_priority, func.count(Job.id))
            .filter(Job.apply_priority.isnot(None))
            .group_by(Job.apply_priority)
            .all()
        )
        by_priority = [PriorityCount(priority=p, count=c) for p, c in prio_rows]

        applied_count = session.query(Job).filter(Job.applied == True).count()

        return StatsResponse(
            total_jobs=total_jobs,
            scored_jobs=scored_jobs,
            unscored_jobs=unscored_jobs,
            avg_score=avg_score,
            max_score=max_score,
            min_score=min_score,
            apply_now_count=apply_now,
            review_first_count=review_first,
            skip_count=skip,
            fintech_count=fintech,
            bank_count=bank,
            nbfc_count=nbfc,
            other_count=other,
            by_verdict=by_verdict,
            by_company_type=by_company_type,
            by_priority=by_priority,
            applied_count=applied_count,
        )
    finally:
        session.close()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _get_sort_column(sort_by: str):
    """Map sort_by string to a SQLAlchemy column."""
    mapping = {
        "relevancy_score": Job.relevancy_score,
        "title": Job.title,
        "company": Job.company,
        "date_posted": Job.date_posted,
        "date_scraped": Job.date_scraped,
        "skills_match_score": Job.skills_match_score,
        "domain_fit_score": Job.domain_fit_score,
        "experience_match_score": Job.experience_match_score,
        "verdict": Job.verdict,
        "apply_priority": Job.apply_priority,
    }
    return mapping.get(sort_by, Job.relevancy_score)


# ------------------------------------------------------------------
# Actions: Scrape & Score (background threads)
# ------------------------------------------------------------------

def _run_scrape():
    """Background thread: run scraper orchestrator."""
    from backend.scrapers.scraper_orchestrator import ScraperOrchestrator
    from backend.scrapers.jobspy_scraper import JobSpyScraper

    try:
        orchestrator = ScraperOrchestrator(engines=[JobSpyScraper()])
        result = orchestrator.run()
        with _action_lock:
            _action_state["scrape"]["running"] = False
            _action_state["scrape"]["last_result"] = result
            _action_state["scrape"]["error"] = None
    except Exception as e:
        with _action_lock:
            _action_state["scrape"]["running"] = False
            _action_state["scrape"]["error"] = str(e)
            _action_state["scrape"]["last_result"] = None
        traceback.print_exc()


def _run_score():
    """Background thread: run scoring pipeline."""
    from backend.scoring.scoring_pipeline import ScoringPipeline

    try:
        pipeline = ScoringPipeline()
        if not pipeline.is_ready:
            reasons = []
            if not pipeline.resume_text:
                reasons.append("No resume found")
            if not pipeline.scorer.is_configured:
                reasons.append("Gemini API key not configured")
            with _action_lock:
                _action_state["score"]["running"] = False
                _action_state["score"]["error"] = "; ".join(reasons)
                _action_state["score"]["last_result"] = None
            return

        result = pipeline.run()
        with _action_lock:
            _action_state["score"]["running"] = False
            _action_state["score"]["last_result"] = result
            _action_state["score"]["error"] = None
    except Exception as e:
        with _action_lock:
            _action_state["score"]["running"] = False
            _action_state["score"]["error"] = str(e)
            _action_state["score"]["last_result"] = None
        traceback.print_exc()


@app.post("/api/scrape")
def trigger_scrape():
    """Trigger a scrape cycle in the background."""
    with _action_lock:
        if _action_state["scrape"]["running"]:
            return {"status": "already_running", "message": "A scrape is already in progress."}
        _action_state["scrape"]["running"] = True
        _action_state["scrape"]["started_at"] = datetime.now(timezone.utc).isoformat()
        _action_state["scrape"]["last_result"] = None
        _action_state["scrape"]["error"] = None

    thread = threading.Thread(target=_run_scrape, daemon=True)
    thread.start()
    return {"status": "started", "message": "Scrape started in background."}


@app.post("/api/score")
def trigger_score():
    """Trigger scoring of all unscored jobs in the background."""
    with _action_lock:
        if _action_state["score"]["running"]:
            return {"status": "already_running", "message": "Scoring is already in progress."}
        _action_state["score"]["running"] = True
        _action_state["score"]["started_at"] = datetime.now(timezone.utc).isoformat()
        _action_state["score"]["last_result"] = None
        _action_state["score"]["error"] = None

    thread = threading.Thread(target=_run_score, daemon=True)
    thread.start()
    return {"status": "started", "message": "Scoring started in background."}


@app.get("/api/actions/status")
def get_actions_status():
    """Get the status of background actions (scrape/score)."""
    with _action_lock:
        return {
            "scrape": {**_action_state["scrape"]},
            "score": {**_action_state["score"]},
        }
