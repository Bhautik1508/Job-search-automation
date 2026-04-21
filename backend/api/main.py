"""
FastAPI application — REST API for the Job Search Automation dashboard.

Endpoints:
    GET   /api/jobs              — Paginated, filterable job list
    GET   /api/jobs/{id}         — Single job detail
    GET   /api/stats             — Dashboard KPI summary
    GET   /api/companies/careers — Careers-page registry (tier-tagged)
    GET   /api/scheduler/status  — Background scheduler liveness
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
from datetime import datetime, timedelta, timezone

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from backend.config import API_KEY, CORS_EXTRA_ORIGINS, FRONTEND_URL, IS_PRODUCTION
from backend.database.models import Job, ScrapeScan, get_engine, get_session_factory, init_db
from backend.api.schemas import (
    CareersLink,
    CompanyTierCount,
    CompanyTypeCount,
    JobListResponse,
    JobResponse,
    PriorityCount,
    SchedulerStatusResponse,
    StatsResponse,
    VerdictCount,
)

# ------------------------------------------------------------------
# App setup
# ------------------------------------------------------------------

app = FastAPI(
    title="Job Search Automation API",
    version="1.0.0",
    description="REST API for the Job Search Automation dashboard",
)

# CORS — strict allowlist.
# Dev origins are always present; prod only trusts FRONTEND_URL and any
# explicit CORS_EXTRA_ORIGINS. We no longer accept any *.vercel.app preview —
# that regex was wide enough that any attacker's preview deploy could send
# credentialed requests.
_cors_origins = [
    "http://localhost:5173",
    "http://localhost:5174",
    "http://localhost:3000",
    "http://127.0.0.1:5173",
    "http://127.0.0.1:5174",
]
if FRONTEND_URL:
    _cors_origins.append(FRONTEND_URL)
_cors_origins.extend(CORS_EXTRA_ORIGINS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key"],
)


# ------------------------------------------------------------------
# API key dependency — protects mutation endpoints.
# ------------------------------------------------------------------

def require_api_key(x_api_key: str | None = Header(default=None)):
    """
    Reject requests without a matching `X-API-Key` header.

    - In production with no API_KEY configured → 503 (fail closed — refuse to
      expose mutation endpoints unauthenticated rather than silently open).
    - In dev with no API_KEY configured → pass (keeps local loops fast).
    - Otherwise → require exact match.
    """
    if not API_KEY:
        if IS_PRODUCTION:
            raise HTTPException(
                status_code=503,
                detail="API_KEY is not configured on this server.",
            )
        return  # dev mode — auth disabled
    if x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid or missing X-API-Key.")

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
    company_tier: str | None = Query(None),
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
        if company_tier:
            query = query.filter(Job.company_tier == company_tier)
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


@app.patch("/api/jobs/{job_id}/applied", dependencies=[Depends(require_api_key)])
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
    """
    Dashboard KPI summary.

    Uses three aggregated queries instead of ~10 scalar COUNTs:
      1. Scalar aggregates (total, scored, applied, avg/max/min score).
      2. GROUP BY apply_priority.
      3. GROUP BY company_type.
      4. GROUP BY verdict.
    """
    session = _get_session()
    try:
        # Query 1: scalar aggregates in one round-trip
        scored_predicate = Job.relevancy_score.isnot(None)
        scalars = session.query(
            func.count(Job.id),
            func.sum(case((scored_predicate, 1), else_=0)),
            func.sum(case((Job.applied == True, 1), else_=0)),
            func.avg(case((scored_predicate, Job.relevancy_score))),
            func.max(case((scored_predicate, Job.relevancy_score))),
            func.min(case((scored_predicate, Job.relevancy_score))),
        ).one()

        total_jobs = scalars[0] or 0
        scored_jobs = int(scalars[1] or 0)
        applied_count = int(scalars[2] or 0)
        avg_score = round(scalars[3] or 0, 1)
        max_score = round(scalars[4] or 0, 1)
        min_score = round(scalars[5] or 0, 1)
        unscored_jobs = total_jobs - scored_jobs

        # Query 2: priority breakdown
        prio_rows = (
            session.query(Job.apply_priority, func.count(Job.id))
            .filter(Job.apply_priority.isnot(None))
            .group_by(Job.apply_priority)
            .all()
        )
        prio_counts = {p: c for p, c in prio_rows}
        by_priority = [PriorityCount(priority=p, count=c) for p, c in prio_rows]

        # Query 3: company-type breakdown
        ctype_rows = (
            session.query(Job.company_type, func.count(Job.id))
            .filter(Job.company_type.isnot(None))
            .group_by(Job.company_type)
            .all()
        )
        ctype_counts = {ct: c for ct, c in ctype_rows}
        by_company_type = [CompanyTypeCount(company_type=ct, count=c) for ct, c in ctype_rows]

        # Query 4: verdict breakdown
        verdict_rows = (
            session.query(Job.verdict, func.count(Job.id))
            .filter(Job.verdict.isnot(None))
            .group_by(Job.verdict)
            .all()
        )
        by_verdict = [VerdictCount(verdict=v, count=c) for v, c in verdict_rows]

        # Query 5: company-tier breakdown (Phase 6)
        tier_rows = (
            session.query(Job.company_tier, func.count(Job.id))
            .filter(Job.company_tier.isnot(None))
            .group_by(Job.company_tier)
            .all()
        )
        tier_counts = {t: c for t, c in tier_rows}
        by_company_tier = [CompanyTierCount(tier=t, count=c) for t, c in tier_rows]

        return StatsResponse(
            total_jobs=total_jobs,
            scored_jobs=scored_jobs,
            unscored_jobs=unscored_jobs,
            avg_score=avg_score,
            max_score=max_score,
            min_score=min_score,
            apply_now_count=prio_counts.get("APPLY_NOW", 0),
            review_first_count=prio_counts.get("REVIEW_FIRST", 0),
            skip_count=prio_counts.get("SKIP", 0),
            fintech_count=ctype_counts.get("fintech", 0),
            bank_count=ctype_counts.get("bank", 0),
            nbfc_count=ctype_counts.get("nbfc", 0),
            other_count=ctype_counts.get("other", 0),
            top_tier_count=tier_counts.get("top_tier", 0),
            unicorn_count=tier_counts.get("unicorn", 0),
            growth_startup_count=tier_counts.get("growth_startup", 0),
            early_startup_count=tier_counts.get("early_startup", 0),
            by_verdict=by_verdict,
            by_company_type=by_company_type,
            by_priority=by_priority,
            by_company_tier=by_company_tier,
            applied_count=applied_count,
        )
    finally:
        session.close()


# ------------------------------------------------------------------
# Companies (Phase 6)
# ------------------------------------------------------------------

@app.get("/api/companies/careers", response_model=list[CareersLink])
def list_careers_links():
    """
    Surface direct careers-page URLs for every company in the tier registry.

    Lets the dashboard show "Open careers page" links without scraping,
    independent of whether scraped jobs from that company already exist in DB.
    """
    from backend.scoring.tier_classifier import careers_links
    return [CareersLink(**entry) for entry in careers_links()]


# ------------------------------------------------------------------
# Scheduler status (Phase 6.5)
# ------------------------------------------------------------------

@app.get("/api/scheduler/status", response_model=SchedulerStatusResponse)
def scheduler_status():
    """
    Liveness indicator for the background scheduler worker.

    Reads the most recent ScrapeScan + latest Job.date_scored rather than
    poking the scheduler process — works whether the scheduler runs in-process,
    as a separate Render worker, or not at all.
    """
    session = _get_session()
    try:
        last_scan = (
            session.query(ScrapeScan)
            .order_by(ScrapeScan.started_at.desc())
            .first()
        )
        last_scored_at = (
            session.query(func.max(Job.date_scored)).scalar()
        )

        cutoff = datetime.now(timezone.utc) - timedelta(hours=24)
        scored_24h = (
            session.query(func.count(Job.id))
            .filter(Job.date_scored >= cutoff)
            .scalar()
        ) or 0

        return SchedulerStatusResponse(
            last_scrape_at=last_scan.started_at if last_scan else None,
            last_scrape_status=last_scan.status if last_scan else None,
            last_scrape_new_jobs=last_scan.jobs_new if last_scan else None,
            last_score_at=last_scored_at,
            scored_jobs_last_24h=int(scored_24h),
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

    try:
        # Use default engines (Apify + JobSpy + optional Instahyre).
        # JobSpy-only doesn't work from cloud IPs (blocked by LinkedIn/Indeed),
        # so the on-demand button needs Apify in the mix — same as the scheduler.
        orchestrator = ScraperOrchestrator()
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


@app.post("/api/scrape", dependencies=[Depends(require_api_key)])
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


@app.post("/api/score", dependencies=[Depends(require_api_key)])
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


@app.get("/api/debug/scrape-check", dependencies=[Depends(require_api_key)])
def debug_scrape_check():
    """
    Diagnose why scrapes return 0 jobs. Reports per-engine config status +
    Apify credit balance + the last scan's error message. Read-only.
    """
    from backend.scrapers.apify_scraper import ApifyScraper
    from backend.scrapers.jobspy_scraper import JobSpyScraper
    from backend.config import (
        APIFY_API_TOKEN, APIFY_ACTORS, APIFY_MAX_CITIES, APIFY_MAX_PORTALS,
        APIFY_PORTAL_PRIORITY, SEARCH_VARIANTS, TARGET_CITIES, INSTAHYRE_ENABLED,
    )

    apify = ApifyScraper()
    apify_balance = apify.check_credit_balance() if apify.is_configured else None

    # If credit_balance came back None with a configured token, the user wants
    # to know *why*. Re-run the inner call without swallowing the exception.
    apify_balance_error = None
    if apify.is_configured and apify_balance is None:
        try:
            from apify_client import ApifyClient
            _c = ApifyClient(apify.api_token)
            _u = _c.user().get()
            apify_balance_error = (
                "user().get() returned empty" if not _u
                else f"unexpected shape: keys={list(_u.keys())[:10]}"
            )
        except Exception as e:
            apify_balance_error = f"{type(e).__name__}: {e}"

    session = _get_session()
    try:
        last_scan = session.query(ScrapeScan).order_by(ScrapeScan.started_at.desc()).first()
        last_scan_info = (
            {
                "id": last_scan.id,
                "started_at": last_scan.started_at.isoformat() if last_scan.started_at else None,
                "status": last_scan.status,
                "portals": last_scan.portals,
                "jobs_found": last_scan.jobs_found,
                "jobs_new": last_scan.jobs_new,
                "jobs_duplicate": last_scan.jobs_duplicate,
                "error_message": last_scan.error_message,
            }
            if last_scan else None
        )
    finally:
        session.close()

    return {
        "apify": {
            "token_present": bool(APIFY_API_TOKEN),
            "token_prefix": (APIFY_API_TOKEN[:10] + "...") if APIFY_API_TOKEN else None,
            "is_configured": apify.is_configured,
            "actors_configured": APIFY_ACTORS,
            "max_cities": APIFY_MAX_CITIES,
            "max_portals": APIFY_MAX_PORTALS,
            "portal_priority": APIFY_PORTAL_PRIORITY,
            "credit_balance": apify_balance,
            "credit_balance_error": apify_balance_error,
        },
        "database": {
            "url_scheme": (str(_engine.url.drivername) if _engine else "not-initialized"),
            "is_sqlite_tmp": str(_engine.url).startswith("sqlite:////tmp") if _engine else False,
        },
        "jobspy": {
            "note": "JobSpy scrapes LinkedIn/Indeed directly — typically blocked on cloud IPs (Render).",
            "is_configured": True,
        },
        "instahyre": {
            "enabled": INSTAHYRE_ENABLED,
            "note": "Disabled by default in production (needs Playwright/Chromium).",
        },
        "search_config": {
            "search_terms": SEARCH_VARIANTS,
            "target_cities": TARGET_CITIES,
        },
        "last_scan": last_scan_info,
    }
