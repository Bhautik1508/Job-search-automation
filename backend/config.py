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
# On Render: copy bundled DB to /tmp (writable) for read/write access.
# Locally: use data/jobs.db directly.
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

# Additional search terms for broader coverage (fintech + banking)
# Keep this list small — each term is scraped across ALL cities × ALL portals.
SEARCH_VARIANTS = [
    "Product Manager",
    "Associate Product Manager",
    "Senior Product Manager",
]

# Phase 4.5: Banking-specific search variants (used by Apify only — more targeted)
# These are run in addition to the base SEARCH_VARIANTS for deeper fintech/banking coverage.
APJFY_BANKING_SEARCH_VARIANTS = [
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
APIFY_ENABLE_BANKING_QUERIES = os.getenv("APIFY_ENABLE_BANKING_QUERIES", "true").lower() == "true"
APIFY_CREDIT_WARNING_THRESHOLD = float(os.getenv("APIFY_CREDIT_WARNING", "0.50"))  # warn when 50% used

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
