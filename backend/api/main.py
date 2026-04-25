"""
FastAPI application — REST API for the Job Search Automation dashboard.

Endpoints:
    GET   /api/jobs              — Paginated, filterable job list
    GET   /api/jobs/{id}         — Single job detail
    GET   /api/stats             — Dashboard KPI summary
    GET   /api/scheduler/status  — Background scheduler liveness
    PATCH /api/jobs/{id}         — Update R2 status (new..hidden)
    PATCH /api/jobs/{id}/applied — Legacy applied toggle (maps to status)
    POST  /api/scrape            — Trigger a scrape cycle
    POST  /api/score             — Trigger scoring of unscored jobs
    POST  /api/enrich-contacts   — Run contact-enrichment pipeline (Phase 7)
    GET   /api/jobs/{id}/contacts — List contacts linked to a job (Phase 7)
    POST  /api/outreach/draft    — Generate an outreach draft (Phase 8)
    GET   /api/jobs/{id}/outreach — List drafts for a job (Phase 8)
    PATCH /api/outreach/{id}     — Update draft status (Phase 8)
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
from backend.database.models import (
    Contact,
    Job,
    JobContact,
    OutreachDraft,
    ScrapeScan,
    get_engine,
    get_session_factory,
    init_db,
)
from backend.api.schemas import (
    CompanyTierCount,
    CompanyTypeCount,
    ContactResponse,
    EnrichmentResponse,
    JobContactsResponse,
    JobListResponse,
    JobOutreachResponse,
    JobResponse,
    JobStatusUpdate,
    OutreachDraftRequest,
    OutreachDraftResponse,
    OutreachStatusUpdate,
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
    """Liveness + scoring-readiness probe.

    Includes resume + Gemini status so deployment env issues
    (missing resume.pdf, missing GEMINI_API_KEY) are visible without
    triggering a full scoring run.
    """
    from backend.config import BACKEND_DIR, GEMINI_API_KEY
    resume_path = BACKEND_DIR / "resume" / "resume.pdf"
    return {
        "status": "ok",
        "service": "job-search-automation",
        "resume_pdf_exists": resume_path.exists(),
        "resume_pdf_path": str(resume_path),
        "gemini_configured": bool(GEMINI_API_KEY) and GEMINI_API_KEY != "your_gemini_api_key_here",
    }


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
    status: str | None = Query(
        None,
        description=(
            "Status filter (R2). Single value (e.g. 'applied') shows just that. "
            "Pass 'all' to include hidden + rejected. Omit to use the default view "
            "(everything except hidden + rejected)."
        ),
    ),
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

        # R2 status filter:
        #   None    → exclude hidden + rejected (the calm default view)
        #   "all"   → no filter
        #   value   → exact match
        if status is None:
            query = query.filter(~Job.status.in_(["hidden", "rejected"]))
        elif status != "all":
            query = query.filter(Job.status == status)

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


@app.patch("/api/jobs/{job_id}", dependencies=[Depends(require_api_key)])
def update_job(job_id: int, payload: JobStatusUpdate):
    """Update a job's R2 status (new | saved | applied | … | hidden)."""
    from backend.database.crud import JOB_STATUSES, update_job_status

    if payload.status not in JOB_STATUSES:
        raise HTTPException(
            status_code=400,
            detail=f"invalid status; expected one of {list(JOB_STATUSES)}",
        )
    session = _get_session()
    try:
        job = update_job_status(session, job_id, payload.status)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"id": job.id, "status": job.status, "applied": job.applied}
    finally:
        session.close()


@app.patch("/api/jobs/{job_id}/applied", dependencies=[Depends(require_api_key)])
def toggle_applied(job_id: int, applied: bool = Query(True)):
    """Legacy R1 endpoint — maps to the R2 status enum.

    `applied=true` → status='applied'; `applied=false` → status='new'.
    Kept for one release so older frontends keep working; R5 removes it.
    """
    from backend.database.crud import update_job_status

    target = "applied" if applied else "new"
    session = _get_session()
    try:
        job = update_job_status(session, job_id, target)
        if job is None:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"id": job.id, "applied": job.applied, "status": job.status}
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

    last_scan_info = None
    last_scan_error = None
    try:
        session = _get_session()
        try:
            last_scan = session.query(ScrapeScan).order_by(ScrapeScan.started_at.desc()).first()
            if last_scan:
                last_scan_info = {
                    "id": last_scan.id,
                    "started_at": last_scan.started_at.isoformat() if last_scan.started_at else None,
                    "status": last_scan.status,
                    "portals": last_scan.portals,
                    "jobs_found": last_scan.jobs_found,
                    "jobs_new": last_scan.jobs_new,
                    "jobs_duplicate": last_scan.jobs_duplicate,
                    "error_message": last_scan.error_message,
                }
        finally:
            session.close()
    except Exception as e:
        last_scan_error = f"{type(e).__name__}: {e}"

    db_info = {"url_scheme": "unknown", "is_sqlite_tmp": False}
    try:
        if _engine is not None:
            db_info = {
                "url_scheme": str(_engine.url.drivername),
                "host": str(_engine.url.host) if _engine.url.host else None,
                "database": str(_engine.url.database) if _engine.url.database else None,
                "is_sqlite_tmp": str(_engine.url).startswith("sqlite:////tmp"),
            }
    except Exception as e:
        db_info["inspect_error"] = f"{type(e).__name__}: {e}"

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
        "database": db_info,
        "last_scan_error": last_scan_error,
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


# ------------------------------------------------------------------
# Contacts (Phase 7)
# ------------------------------------------------------------------

def _contact_row_to_response(
    contact: Contact,
    link: JobContact | None = None,
) -> ContactResponse:
    """Assemble a ContactResponse from a Contact + optional JobContact."""
    return ContactResponse(
        id=contact.id,
        name=contact.name,
        title=contact.title,
        company=contact.company,
        linkedin_url=contact.linkedin_url,
        email=contact.email,
        role_type=contact.role_type,
        confidence=contact.confidence,
        source_provider=contact.source_provider,
        link_provider=link.provider if link else None,
        link_confidence=link.confidence if link else None,
        last_enriched_at=contact.last_enriched_at,
    )


@app.get("/api/jobs/{job_id}/contacts", response_model=JobContactsResponse)
def list_job_contacts(job_id: int):
    """Return all contacts linked to a specific job."""
    from backend.database.crud import get_contacts_for_job

    session = _get_session()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        pairs = get_contacts_for_job(session, job_id)
        contacts = [_contact_row_to_response(c, link) for c, link in pairs]
        return JobContactsResponse(
            job_id=job_id,
            company=job.company,
            contacts=contacts,
        )
    finally:
        session.close()


@app.post(
    "/api/enrich-contacts",
    response_model=EnrichmentResponse,
    dependencies=[Depends(require_api_key)],
)
def enrich_contacts(
    job_id: int | None = Query(None, description="Enrich a single job when set"),
    limit: int = Query(20, ge=1, le=200, description="Max eligible jobs per batch"),
):
    """
    Run the contact-enrichment pipeline.

    - With `?job_id=`: enrich only that job (admin/debug path).
    - Without `job_id`: enrich up to `limit` unapplied jobs that are
      eligible per verdict + tier config.
    """
    from backend.contacts.enrichment_pipeline import EnrichmentPipeline

    session = _get_session()
    try:
        pipeline = EnrichmentPipeline(session)

        if job_id is not None:
            job = session.query(Job).filter(Job.id == job_id).first()
            if not job:
                raise HTTPException(status_code=404, detail="Job not found")
            result = pipeline.enrich_job(job)
        else:
            candidates = (
                session.query(Job)
                .filter(Job.applied == False)  # noqa: E712 — SQLAlchemy idiom
                .filter(Job.verdict.isnot(None))
                .order_by(Job.relevancy_score.desc().nullslast())
                .limit(limit)
                .all()
            )
            result = pipeline.run(candidates)

        return EnrichmentResponse(status="completed", **result.to_dict())
    finally:
        session.close()


# ------------------------------------------------------------------
# Outreach (Phase 8)
# ------------------------------------------------------------------

@app.post(
    "/api/outreach/draft",
    response_model=OutreachDraftResponse,
    dependencies=[Depends(require_api_key)],
)
def generate_outreach_draft(payload: OutreachDraftRequest):
    """
    Generate (or regenerate) an outreach draft for a (job, contact, channel).

    Idempotent on the (job_id, contact_id, channel) tuple — rerunning replaces
    the existing row's body/subject/tone in place rather than accumulating
    variants.
    """
    from backend.database.crud import (
        get_outreach_drafts_for_job,  # noqa: F401 — keeps import graph explicit
        upsert_outreach_draft,
    )
    from backend.outreach.generator import OutreachGenerator

    session = _get_session()
    try:
        job = session.query(Job).filter(Job.id == payload.job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        contact = session.query(Contact).filter(Contact.id == payload.contact_id).first()
        if not contact:
            raise HTTPException(status_code=404, detail="Contact not found")

        generator = OutreachGenerator()
        if not generator.is_configured:
            raise HTTPException(
                status_code=503,
                detail="Gemini API key not configured — cannot generate outreach.",
            )

        try:
            result = generator.generate(
                job=job,
                contact=contact,
                channel=payload.channel,
                tone=payload.tone,
            )
        except ValueError as e:
            # Invalid channel/tone — map to 400 for a clean API contract.
            raise HTTPException(status_code=400, detail=str(e))

        if result is None:
            raise HTTPException(
                status_code=503,
                detail="Outreach generator returned no result.",
            )

        draft = upsert_outreach_draft(
            session,
            job_id=job.id,
            contact_id=contact.id,
            channel=result.channel,
            tone=result.tone,
            subject=result.subject,
            body=result.body,
            model=result.model,
            case_study_link=result.case_study_link,
            case_study_attachment=result.case_study_attachment,
        )
        return OutreachDraftResponse.model_validate(draft)
    finally:
        session.close()


@app.get(
    "/api/jobs/{job_id}/outreach",
    response_model=JobOutreachResponse,
)
def list_job_outreach_drafts(job_id: int):
    """Return all outreach drafts generated for a specific job."""
    from backend.database.crud import get_outreach_drafts_for_job

    session = _get_session()
    try:
        job = session.query(Job).filter(Job.id == job_id).first()
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        drafts = get_outreach_drafts_for_job(session, job_id)
        return JobOutreachResponse(
            job_id=job_id,
            drafts=[OutreachDraftResponse.model_validate(d) for d in drafts],
        )
    finally:
        session.close()


@app.patch(
    "/api/outreach/{draft_id}",
    response_model=OutreachDraftResponse,
    dependencies=[Depends(require_api_key)],
)
def update_outreach_draft_status(draft_id: int, payload: OutreachStatusUpdate):
    """
    Edit a draft (R3): status, body, and/or subject. At least one must be set.

    Status moves through draft → sent → replied. Body/subject let the user
    tweak copy before copying it out.
    """
    from backend.database.crud import update_outreach_draft

    if payload.status is None and payload.body is None and payload.subject is None:
        raise HTTPException(
            status_code=400,
            detail="At least one of status, body, subject must be provided.",
        )

    if payload.status is not None:
        valid = {"draft", "sent", "replied"}
        if payload.status not in valid:
            raise HTTPException(
                status_code=400,
                detail=f"Invalid status; expected one of {sorted(valid)}",
            )

    session = _get_session()
    try:
        draft = update_outreach_draft(
            session,
            draft_id,
            status=payload.status,
            body=payload.body,
            subject=payload.subject,
        )
        if draft is None:
            raise HTTPException(status_code=404, detail="Draft not found")
        return OutreachDraftResponse.model_validate(draft)
    finally:
        session.close()


@app.post("/api/admin/prune-out-of-region", dependencies=[Depends(require_api_key)])
def prune_out_of_region_jobs(dry_run: bool = True):
    """
    Delete jobs whose location doesn't match ALLOWED_LOCATION_KEYWORDS.
    Pass ?dry_run=false to actually delete; default is dry-run (reports count
    but keeps rows). One-shot cleanup for regional scoping changes.
    """
    from backend.config import ALLOWED_LOCATION_KEYWORDS

    if not ALLOWED_LOCATION_KEYWORDS:
        return {"deleted": 0, "reason": "ALLOWED_LOCATION_KEYWORDS is empty — nothing to prune."}

    session = _get_session()
    try:
        all_jobs = session.query(Job).all()
        to_delete = [
            j for j in all_jobs
            if not any(k in (j.location or "").lower() for k in ALLOWED_LOCATION_KEYWORDS)
        ]
        sample = [
            {"id": j.id, "title": j.title, "location": j.location}
            for j in to_delete[:10]
        ]

        if not dry_run:
            for j in to_delete:
                session.delete(j)
            session.commit()

        return {
            "dry_run": dry_run,
            "total_jobs": len(all_jobs),
            "out_of_region_count": len(to_delete),
            "kept_count": len(all_jobs) - len(to_delete),
            "allowed_keywords": ALLOWED_LOCATION_KEYWORDS,
            "sample": sample,
            "message": (
                "Dry run — re-run with ?dry_run=false to delete."
                if dry_run else f"Deleted {len(to_delete)} jobs."
            ),
        }
    finally:
        session.close()
