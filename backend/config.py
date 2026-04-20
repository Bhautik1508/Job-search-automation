"""
Configuration module — loads environment variables and provides project-wide settings.
"""

import os
import shutil
from pathlib import Path
from dotenv import load_dotenv

# ------------------------------------------------------------------
# Paths
# ------------------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # Job-search-automation/
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = PROJECT_ROOT / "data"

# Ensure the data directory exists
DATA_DIR.mkdir(parents=True, exist_ok=True)

# Load .env from project root
load_dotenv(PROJECT_ROOT / ".env")

# ------------------------------------------------------------------
# Environment
# ------------------------------------------------------------------
IS_PRODUCTION = os.getenv("RENDER", "") != "" or os.getenv("ENVIRONMENT", "") == "production"

# ------------------------------------------------------------------
# Database
# ------------------------------------------------------------------
# On Render (SQLite fallback): copy bundled DB to /tmp (writable).
# Locally: use data/jobs.db directly.
# If DATABASE_URL is set to Postgres/MySQL/etc., the SQLite copy path is skipped.
def _get_database_url() -> str:
    explicit = os.getenv("DATABASE_URL")
    if explicit:
        return explicit

    source_db = DATA_DIR / "jobs.db"

    if IS_PRODUCTION:
        tmp_db = Path("/tmp/jobs.db")
        # Copy bundled DB to /tmp if it doesn't exist yet (cold start)
        if not tmp_db.exists() and source_db.exists():
            shutil.copy2(source_db, tmp_db)
            print(f"📦 Copied bundled DB to {tmp_db}")
        return f"sqlite:///{tmp_db}"

    return f"sqlite:///{source_db}"

DATABASE_URL = _get_database_url()


# ------------------------------------------------------------------
# Scraping
# ------------------------------------------------------------------
SEARCH_TERM = os.getenv("SEARCH_TERM", "Product Manager")
TARGET_CITIES = [
    city.strip()
    for city in os.getenv("TARGET_CITIES", "Bangalore,Mumbai,Delhi NCR,Hyderabad,Pune").split(",")
]
SCRAPE_INTERVAL_HOURS = int(os.getenv("SCRAPE_INTERVAL_HOURS", "4"))
# Scoring runs on its own cadence — slightly offset from scrape so fresh jobs
# from a scrape cycle get picked up by the next score cycle instead of both
# contending for DB writes at the same instant.
SCORE_INTERVAL_HOURS = int(os.getenv("SCORE_INTERVAL_HOURS", "4"))
SCORE_OFFSET_MINUTES = int(os.getenv("SCORE_OFFSET_MINUTES", "30"))
SCHEDULER_TIMEZONE = os.getenv("SCHEDULER_TIMEZONE", "Asia/Kolkata")

# Additional search terms for broader coverage (fintech + banking)
# Keep this list small — each term is scraped across ALL cities × ALL portals.
SEARCH_VARIANTS = [
    "Product Manager",
    "Associate Product Manager",
    "Senior Product Manager",
]

# Phase 4.5: Banking-specific search variants (used by Apify only — more targeted)
# These are run in addition to the base SEARCH_VARIANTS for deeper fintech/banking coverage.
APIFY_BANKING_SEARCH_VARIANTS = [
    "Product Manager Fintech",
    "Product Manager Banking",
    "Product Manager Payments",
    "Product Manager Digital Banking",
    "Product Manager Lending",
    "PM UPI",
    "PM Credit Cards",
]

# ------------------------------------------------------------------
# Title relevancy filter (post-scrape)
# ------------------------------------------------------------------
# Jobs MUST contain at least one of these keywords in the title (case-insensitive)
RELEVANT_TITLE_KEYWORDS = [
    "product manager",
    "product lead",
    "product owner",
    "product head",
    "product director",
    "product analyst",
    "product strategist",
    "product management",
    "apm",       # Associate Product Manager
    "group pm",
    "gpm",       # Group Product Manager
    "vp product",
    "vp of product",
    "chief product officer",
    "cpo",
]

# Jobs containing any of these keywords are ALWAYS rejected (case-insensitive)
IRRELEVANT_TITLE_KEYWORDS = [
    "software engineer",
    "software developer",
    "frontend",
    "front end",
    "front-end",
    "backend",
    "back end",
    "back-end",
    "full stack",
    "fullstack",
    "devops",
    "data engineer",
    "data scientist",
    "machine learning",
    "ml engineer",
    "sde",
    "sre",
    "qa engineer",
    "test engineer",
    "automation engineer",
    "ios developer",
    "android developer",
    "mobile developer",
    "ui developer",
    "ux designer",
    "graphic designer",
    "sales executive",
    "sales manager",
    "account executive",
    "account manager",
    "business development",
    "bdr",
    "recruiter",
    "hr manager",
    "content writer",
    "copywriter",
    "marketing manager",
    "digital marketing",
    "seo specialist",
    "customer support",
    "customer success",
    "network engineer",
    "system administrator",
    "database administrator",
    "cloud engineer",
    "security engineer",
    "production manager",   # manufacturing, not PM
    "production supervisor",
    "production engineer",
    "production planner",
]

# ------------------------------------------------------------------
# Apify
# ------------------------------------------------------------------
APIFY_API_TOKEN = os.getenv("APIFY_API_TOKEN", "")

# Apify Actor IDs (community actors from the Apify Store)
APIFY_ACTORS = {
    "linkedin": "hMvNSpz3JnHgl5jkh",        # LinkedIn Jobs Scraper
    "naukri": "karamelo~naukri-scraper",       # Naukri Scraper
    "indeed": "misceres~indeed-scraper",      # Indeed Scraper
    "glassdoor": "bebity~glassdoor-scraper",  # Glassdoor Scraper
}

# Phase 4.5: Apify Actor tuning — portal-specific timeouts & limits
APIFY_ACTOR_TIMEOUT = int(os.getenv("APIFY_ACTOR_TIMEOUT", "180"))   # seconds
APIFY_MAX_ITEMS_PER_ACTOR = int(os.getenv("APIFY_MAX_ITEMS", "50"))  # items per actor run
# Phase 5: banking query expansion is OFF by default — it multiplies actor
# runs by ~7× and quickly exhausts Apify credits. Enable explicitly when
# you need deeper fintech coverage.
APIFY_ENABLE_BANKING_QUERIES = os.getenv("APIFY_ENABLE_BANKING_QUERIES", "false").lower() == "true"
APIFY_CREDIT_WARNING_THRESHOLD = float(os.getenv("APIFY_CREDIT_WARNING", "0.50"))  # warn when 50% used

# Phase 5: Apify fan-out limits — cap cities × portals per cycle.
# A cycle of 3 terms × 5 cities × 4 portals = 60 runs pre-limit;
# capping to 2 cities × 2 portals → 12 runs (~80% reduction).
APIFY_MAX_CITIES = int(os.getenv("APIFY_MAX_CITIES", "2"))
APIFY_MAX_PORTALS = int(os.getenv("APIFY_MAX_PORTALS", "2"))
# Which portals to prioritise when APIFY_MAX_PORTALS < len(APIFY_ACTORS).
# Listed in descending priority order.
APIFY_PORTAL_PRIORITY = [
    p.strip()
    for p in os.getenv("APIFY_PORTAL_PRIORITY", "linkedin,naukri,indeed,glassdoor").split(",")
    if p.strip()
]

# ------------------------------------------------------------------
# Gemini (Phase 2)
# ------------------------------------------------------------------
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")
GEMINI_RPM_LIMIT = int(os.getenv("GEMINI_RPM_LIMIT", "15"))

# Scoring weights (must sum to 1.0)
SCORE_WEIGHT_SKILLS = float(os.getenv("SCORE_WEIGHT_SKILLS", "0.30"))
SCORE_WEIGHT_DOMAIN = float(os.getenv("SCORE_WEIGHT_DOMAIN", "0.25"))
SCORE_WEIGHT_EXPERIENCE = float(os.getenv("SCORE_WEIGHT_EXPERIENCE", "0.20"))
SCORE_WEIGHT_SENIORITY = float(os.getenv("SCORE_WEIGHT_SENIORITY", "0.15"))
SCORE_WEIGHT_RECENCY = float(os.getenv("SCORE_WEIGHT_RECENCY", "0.10"))

# Resume path (Phase 2)
RESUME_PATH = os.getenv("RESUME_PATH", str(BACKEND_DIR / "resume" / "resume.pdf"))

# Max chars of job description sent to Gemini — caps token cost per scoring call.
GEMINI_JD_MAX_CHARS = int(os.getenv("GEMINI_JD_MAX_CHARS", "3000"))


# ------------------------------------------------------------------
# API auth & CORS (Phase 5)
# ------------------------------------------------------------------
# Mutation endpoints (/api/scrape, /api/score, /api/jobs/{id}/applied) require
# this key via the `X-API-Key` header. If unset AND the service is running in
# production, mutation endpoints return 503 — fail closed. In local dev, an
# empty key disables the check to keep the developer loop friction-free.
API_KEY = os.getenv("API_KEY", "")

# CORS allowlist. In dev we always permit localhost. In production, only the
# explicit FRONTEND_URL is permitted — no more blanket *.vercel.app regex.
FRONTEND_URL = os.getenv("FRONTEND_URL", "")
# Optional: comma-separated additional origins (e.g. preview deploys you own).
CORS_EXTRA_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_EXTRA_ORIGINS", "").split(",")
    if o.strip()
]


# ------------------------------------------------------------------
# Alerts (Phase 4)
# ------------------------------------------------------------------
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# ------------------------------------------------------------------
# Instahyre (Phase 4.4)
# ------------------------------------------------------------------
INSTAHYRE_EMAIL = os.getenv("INSTAHYRE_EMAIL", "")
INSTAHYRE_PASSWORD = os.getenv("INSTAHYRE_PASSWORD", "")
INSTAHYRE_HEADLESS = os.getenv("INSTAHYRE_HEADLESS", "true").lower() == "true"
INSTAHYRE_TIMEOUT_MS = int(os.getenv("INSTAHYRE_TIMEOUT_MS", "30000"))
INSTAHYRE_MAX_PAGES = int(os.getenv("INSTAHYRE_MAX_PAGES", "5"))

# ------------------------------------------------------------------
# JobSpy rate-limit settings
# ------------------------------------------------------------------
JOBSPY_RESULTS_PER_SITE = 30  # Results to fetch per site per city
JOBSPY_HOURS_OLD = 72         # Only fetch jobs posted in the last N hours
