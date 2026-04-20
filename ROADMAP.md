# Job Search Automation — Phasewise Roadmap

Goal: automate end-to-end job search for **Product Manager** roles at top companies + startups.
Capabilities: scraping → relevancy scoring → hiring manager/recruiter discovery → AI-drafted outreach with proof-of-work → referral matching via Happenstance → application tracking.

---

## Current Build Review (Phases 1–4.5 — shipped)

**Scraping** — [scraper_orchestrator.py](backend/scrapers/scraper_orchestrator.py) runs three engines:
- [jobspy_scraper.py](backend/scrapers/jobspy_scraper.py) — LinkedIn, Indeed, Google Jobs
- [apify_scraper.py](backend/scrapers/apify_scraper.py) — LinkedIn, Naukri, Indeed, Glassdoor (community actors)
- [instahyre_scraper.py](backend/scrapers/instahyre_scraper.py) — Playwright-based, credentials-gated

Fuzzy dedup + title relevance filter (allow/block keyword lists in [config.py](backend/config.py)).

**Scoring** — [scoring_pipeline.py](backend/scoring/scoring_pipeline.py) uses Gemini with resume ([resume.pdf](backend/resume/resume.pdf)) + weighted rubric (skills 30, domain 25, exp 20, seniority 15, recency 10) + fintech/bank domain bonus via [company_classifier.py](backend/scoring/company_classifier.py). Gemini model-fallback chain on quota exhaustion.

**Dashboard** — FastAPI ([main.py](backend/api/main.py)) + React ([App.jsx](frontend/src/App.jsx)) with KPIs, filters, applied toggle, background scrape/score triggers. Deployed: Render (API) + Vercel (UI), SQLite DB.

**Stubs / gaps** — [alerts/](backend/alerts/) empty, [scheduler/](backend/scheduler/) empty (despite `apscheduler` in requirements), no hiring-manager discovery, no outreach drafting, no Happenstance integration, no company-stage signal.

---

## Issues Found in Existing Build

### Critical (data loss risk)
1. **SQLite on Render is ephemeral.** [config.py:33-48](backend/config.py#L33-L48) copies `data/jobs.db` → `/tmp/jobs.db` on cold start. Render restarts wipe `/tmp` → every scrape run in production is lost on the next restart. Only the bundled-at-build-time DB survives.
2. **No Alembic migrations.** [backend/database/migrations/](backend/database/migrations/) is empty. `init_db` only creates missing tables, never alters existing ones — any schema change (adding `contacts`, `outreach_drafts`, etc.) will silently break prod.

### Performance
3. **`bulk_insert_jobs` is N+1.** [crud.py:46-55](backend/database/crud.py#L46-L55) calls `get_job_by_dedup_hash` once per row — should batch into one `IN (…)` query.
4. **`/api/stats` fires ~10 separate COUNT queries.** [main.py:206-287](backend/api/main.py#L206-L287) — collapse into 2–3 aggregated `GROUP BY` queries.
5. **No DB indexes on hot columns** — `relevancy_score`, `apply_priority`, `company_type`, `verdict`, `date_scraped`. All filter queries in [main.py:109-170](backend/api/main.py#L109-L170) do full scans.
6. **Apify combinatorial blowup.** [apify_scraper.py:107-143](backend/scrapers/apify_scraper.py#L107-L143) runs `(3 base + 7 banking) × 5 cities × 4 portals = 200 actor calls` sequentially with 180s timeout each. Can exceed 1 hour + burns Apify credits.
7. **Engines run strictly sequentially** in [scraper_orchestrator.py:124-140](backend/scrapers/scraper_orchestrator.py#L124-L140) — JobSpy, Apify, Instahyre are independent.
8. **Title filter runs after dedup** — [scraper_orchestrator.py:72-80](backend/scrapers/scraper_orchestrator.py#L72-L80). Filter first to shrink dedup set.
9. **Job description not truncated before Gemini** — burns tokens on long JDs. Cap at ~3000 chars.
10. **Resume re-parsed every pipeline run** — cache parsed text.

### Correctness / security
11. **CORS regex `https://.*\.vercel\.app`** ([main.py:64](backend/api/main.py#L64)) allows any Vercel project to call your API — lock to the specific deployment URL.
12. **API is unauthenticated.** Anyone with the Render URL can trigger `/api/scrape` / `/api/score` and burn Apify + Gemini credits.
13. **Typo: `APJFY_BANKING_SEARCH_VARIANTS`** in [config.py:73](backend/config.py#L73) and [apify_scraper.py:25](backend/scrapers/apify_scraper.py#L25).
14. **Empty `alerts/` and `scheduler/` dirs** — `apscheduler` in requirements but unused.

---

## Phasewise Plan

### Phase 5 — Hardening (blocker for later phases)
Fix data loss, performance, and security before building on top.

1. **Persistent DB on Render** — switch from ephemeral SQLite to Render Postgres (free tier). Update [config.py](backend/config.py) to use `DATABASE_URL` properly. One-time migrate `data/jobs.db` rows.
2. **Alembic migrations** — initialize in [backend/database/migrations/](backend/database/migrations/), baseline current schema. Required before Phase 7 adds new tables.
3. **Index hot columns** — `relevancy_score`, `apply_priority`, `company_type`, `verdict`, `date_scraped`, `(applied, relevancy_score)`.
4. **Fix `bulk_insert_jobs` N+1** — batched `IN` query for existing hashes.
5. **Collapse `/api/stats`** — 2–3 `GROUP BY` aggregations.
6. **Parallelize orchestrator engines** — thread pool (JobSpy, Apify, Instahyre concurrently).
7. **Prune Apify fan-out** — cap to `N_terms × top_2_cities × 2_portals` by default; banking variants behind manual toggle. Target ≤40 actor calls per cycle.
8. **Filter-before-dedup** in orchestrator.
9. **Truncate JD to 3000 chars** before Gemini; cache resume text.
10. **Secure the API** — lock CORS to the Vercel URL; require `X-API-Key` header on `/api/scrape` and `/api/score`.
11. **Fix `APJFY_` typo** → `APIFY_`.
12. **Wire APScheduler** — orchestrator on `SCRAPE_INTERVAL_HOURS`, auto-trigger scoring. Run as a Render background worker (not in the web process).

### Phase 6 — Coverage & Company Tier Enrichment
Rank by company quality, not just JD fit.

1. **Add sources** — Wellfound (AngelList) for startups, YC Work-at-a-Startup, Hirect, Cutshort, and direct careers pages for top PM shops (Razorpay, CRED, Zerodha, PhonePe, Swiggy, Flipkart, Google, Meta, Stripe).
2. **Company tier column** — `company_tier` (`top_tier` / `unicorn` / `growth_startup` / `early_startup` / `other`), `funding_stage`, `headcount`. Curated list + optional Crunchbase lookup.
3. **Resume-derived query expansion** — pull skill/keyword variants from parsed resume instead of hard-coded `SEARCH_VARIANTS`.
4. **Dashboard filter** — company-tier chip in [FilterBar.jsx](frontend/src/components/FilterBar.jsx).

### Phase 7 — Hiring Manager & Recruiter Discovery
For each `APPLY_NOW` job, surface 1–3 contacts.

1. **New tables (via Alembic)** — `contacts` (name, title, linkedin_url, email, company, role_type: `hm`/`recruiter`/`referral`, `confidence`), `job_contacts` (job_id ↔ contact_id).
2. **Primary source** — Apollo.io API for bulk recruiter/HM search by company + title (`Product`, `Talent`, `Recruiter`, `HRBP`). 50 free credits/month.
3. **Fallbacks** — Apify LinkedIn profile scraper (`harvestapi~linkedin-profile-scraper`); Hunter.io for email patterns.
4. **Pipeline** — after scoring, for jobs with verdict ≥ GOOD_FIT in `{top_tier, unicorn, growth_startup}`, enqueue contact enrichment. Cache by company for 30 days.
5. **Cost guardrails** — per-company credit cap, daily cap across providers.

### Phase 8 — AI-Drafted Outreach
One-click ready message per contact, with proof-of-work.

1. **Proof-of-work library** — [backend/portfolio/](backend/portfolio/) with case studies tagged by domain (fintech, payments, lending, consumer, SaaS) + skill (growth, 0→1, platform, data).
2. **Gemini message generator** — takes `{resume, job, contact, top_portfolio_items}` → produces:
   - LinkedIn connection note (≤200 chars)
   - LinkedIn InMail (300–500 chars)
   - Cold email (subject + body + attachment suggestions)
   - Referral-ask variant (shorter, warmer)
3. **Tone presets** — `founder-pitch`, `peer-PM`, `recruiter-formal`.
4. **New table** — `outreach_drafts` (job_id, contact_id, channel, subject, body, attachments, status: `draft`/`sent`/`replied`).
5. **UI** — "Draft outreach" button per job → modal with contact list + editable drafts + copy-to-clipboard + LinkedIn deep link + `mailto:` with body prefilled.

### Phase 9 — Referral Engine (Happenstance)
For each relevant job, find warm intros from the user's network.

1. **Happenstance import** — API / CSV export of LinkedIn connections → `connections` table (name, company, title, relationship_strength, last_interaction).
2. **Matcher** — for every `APPLY_NOW` job, rank 1st-degree connections at the company (and parents/subsidiaries) by title seniority + tenure + recency.
3. **Warm draft** — Gemini produces a message referencing shared context (mutual company, school, last interaction), job link, 2-line pitch.
4. **2nd-degree fallback** — surface mutuals ("X → Y at company") with "ask X for intro to Y" draft.
5. **Referrals tab** per job — connection cards + copy-message + LinkedIn DM deep link.

### Phase 10 — Application Tracker & Funnel
1. **Kanban board** — Shortlisted → Applied → Outreach Sent → Recruiter Call → Interview → Offer/Rejected. Uses existing `application_status`.
2. **Follow-up reminders** — on `Outreach Sent`, auto-create reminder at +4 days. Shown in dashboard (no push notifications).
3. **Daily brief widget** on dashboard — "3 new APPLY_NOW, 2 with 1st-degree referrals" with drafted outreach inline.
4. **Funnel analytics** — response rate by channel / tier / template → feeds back into Phase 8 prompt tuning.

### Phase 11 — Smart Prep & Learner
1. **Resume-JD gap coach** — per job, Gemini suggests 2–3 resume bullets to add/emphasize (uses existing `missing_skills`).
2. **Interview prep pack** — one-click generate: company research brief + likely PM case questions based on product + recent-news scrape.
3. **A/B outreach learner** — after 20+ sent messages, cluster by reply-yes rate, bias future drafts.

### Phase 12 — Polish
1. Gmail API listener to auto-log replies (optional).
2. Proper user auth if ever shared.
3. Test coverage for Phases 7–9.

---

## Non-goals / Explicitly Dropped
- **Telegram alerts** — all surfacing happens in-dashboard.
- **Push notifications** — follow-ups and daily briefs are pulled, not pushed.

---

## Phase Dependencies
- Phase 5 (hardening) blocks Phases 7+ — can't add `contacts` / `outreach_drafts` tables without Alembic + Postgres.
- Phase 6 (company tier) should precede Phase 7 — contact enrichment is prioritized by tier to conserve credits.
- Phase 8 (outreach drafting) depends on Phase 7 (contacts) and portfolio library.
- Phase 9 (referrals) is independent of Phase 7 — can run in parallel.
- Phase 10 tracker is useful from Phase 8 onwards.

---

## Quick Reference — Recommended Build Order
1. Phase 5 critical trio: Postgres migration, Alembic baseline, API-key auth
2. Phase 5 remaining optimizations (indexes, N+1 fix, /stats collapse, parallel engines, Apify pruning)
3. Phase 5 scheduler
4. Phase 6 company tier + sources
5. Phase 7 contact discovery
6. Phase 8 outreach drafting (needs portfolio library prepared)
7. Phase 9 Happenstance referrals
8. Phase 10 tracker + daily brief
9. Phase 11 prep & learner
10. Phase 12 polish
