# Job Hunt — Revamp Plan (v2)

The v1 build (Phases 1–8 in [ROADMAP.md](ROADMAP.md)) sprawled across enrichment guardrails, tier classifiers, careers registries, schedulers, and debug endpoints. v2 strips that down to the workflow you actually use, ships a calmer UI, and adds two missing pieces (warm-connection referrals, real application tracking).

This file is a plan. **No code is written yet** — sign off on phases first, then we implement one at a time.

---

## Target Flow (7 steps)

1. **Scrape** jobs from LinkedIn / Indeed / Naukri etc.
2. **Score** scraped jobs against the resume.
3. **Find contacts** — HM + recruiter at the company.
4. **Draft cold outreach** — message/email tuned to the company, citing one portfolio case study (link or attachment).
5. **Draft referral asks** — pull warm connections from Happenstance, draft a LinkedIn DM asking for an intro to the HM at $company.
6. **Track applications** through real states (saved → applied → in-process → offer/reject) and **delete jobs** that aren't worth pursuing.
7. **Calmer UI** — light theme, subtle palette, single-page workflow.

---

## Audit — Map Each Step to Today's Code

| New step | Reuse | Augment | Remove |
|---|---|---|---|
| 1. Scrape | `scraper_orchestrator`, `jobspy_scraper`, `apify_scraper`, `instahyre_scraper` | — | careers registry, careers UI |
| 2. Score | `scoring_pipeline`, `gemini_scorer`, resume parsing | — | `tier_classifier`, `company_classifier` (over-engineered for one user) |
| 3. Contacts | Apollo + Hunter clients, role-aware HM keywords (just shipped) | Drop the eligibility-gates UI; v2 always allows manual lookup | `cost_guardrails` complexity (keep daily cap, drop tier gate) |
| 4. Cold outreach | `OutreachGenerator`, `portfolio_registry`, `OutreachDraft` model | Add **case-study attachment**: portfolio item carries a public URL or PDF path the email can link/attach | — |
| 5. Referrals (Happenstance) | — | **Net new.** Connection import, company match, referral-ask drafter | — |
| 6. Tracking + delete | `applied` toggle | Replace boolean with status enum + add hide/delete | scheduler dashboard widget (you're not using it) |
| 7. UI | React app, FastAPI, table layout | Light theme, simpler IA, focused single-page flow | `KpiCards`, `CareersLinks`, `SchedulerIndicator`, action toasts cluster |

**Things to delete outright** (dead/unused code we're carrying):
- `backend/alerts/` (empty stub from v1 plan)
- Careers-page registry + scraper + frontend widget
- Scheduler liveness indicator
- `/api/debug/scrape-check` once stable
- Tier classification (`company_tier`, `funding_stage`, `headcount_band` columns) — keep them in DB to avoid migration churn but stop populating/displaying

---

## Phases

Each phase is independently shippable. Land them in order; each leaves the app in a working state.

### Phase R1 — Pruning + Light Theme (1–2 days)

**Why first:** removes noise from the UI so subsequent panels (referrals, tracking) have room to breathe. Also the user-visible request that's most "I can feel it immediately."

**Scope**
- New design tokens: light background (`#fafafa`/`#fff`), one accent (e.g. slate-blue), zero neon. WCAG-AA contrast.
- Drop: `KpiCards`, `CareersLinks`, `SchedulerIndicator`, action-toasts cluster.
- Replace top bar with: `[Scrape] [Score]` buttons + a single status line that shows the most recent action result.
- Job table: keep score, title, company, location, posted-date, status, actions. Lose the rest.
- Delete careers-related backend endpoints + tables (or keep table dormant; drop endpoint).
- ScoreModal: keep, but restyle for the new palette.

**Open questions**
- Light only, or light + dark toggle? (Default: light only, simpler.)
- Type scale and accent — I'll propose 2–3 options with screenshots before we commit.

### Phase R2 — Application Tracking + Delete (1 day)

**Scope**
- Replace `Job.applied: bool` with `Job.status: enum` — `new | saved | applied | interviewing | offer | rejected | hidden`.
  Migration adds `status` column, backfills from `applied`. Keep `applied` for one release as a shadow column, drop in R3.
- Endpoints: `PATCH /api/jobs/{id}` accepting `{status}`. Soft-delete (`status=hidden`).
- UI: status dropdown in the row + "hide" affordance. Filter chips for status.
- Default filter excludes `hidden` and `rejected`.

**Open questions**
- Hard delete vs. soft (`status=hidden`)? Soft is safer; hard is cleaner. (Default: soft.)

### Phase R3 — Outreach UX Upgrade + Case-Study Attach (1–2 days)

**Scope**
- Allow editing the draft body before copy/send (textarea + save).
- Each `PortfolioItem` gains optional `url` (public link) and `attachment_path` (PDF in `backend/portfolio/`).
- Generator output includes a `case_study_link` field rendered next to the draft body. Email channel can include it inline; LinkedIn channels surface it as "Attach this when you send."
- Add 2–3 real case-study items to `default_registry()` based on your actual portfolio (you provide).

**Open questions**
- Where do the case studies live? Notion public page? PDF in repo? S3? (Default: PDF in repo, public Notion link as fallback.)

### Phase R4 — Happenstance Referral Layer (3–4 days)

**Scope** — biggest unknown; we'll spike before committing.
- Spike (½ day): does Happenstance have an API or only a CSV export? If API, OAuth or token-based?
- Connection model: new table `connections (id, name, company, current_title, linkedin_url, source, last_synced_at)`.
- Import path:
  - **API path** if available — periodic sync.
  - **CSV path** as fallback — drag-and-drop upload in the UI.
- Match logic: for a given job, surface connections at `job.company` (exact + fuzzy on company name).
- New draft channel: `referral_ask`. Tone: "warm peer ask" (already a tone in v1, but the prompt is generic). Rewrite prompt to:
  - Reference your shared connection point.
  - Ask for an intro to the named HM (output of step 3).
  - Stay under LinkedIn DM length.
- UI: in the job's contacts panel, a third tab/row "Warm Connections" listing matching Happenstance contacts with a "Draft Intro Ask" button.

**Open questions**
- Happenstance access — do you have export rights? What's the format? (Need a sample file or API doc to spike against.)
- What signals beyond company match should we surface? (mutual school, past employer, etc.)

### Phase R5 — Simplify Backend (1 day, last)

**Scope** — once R1–R4 are stable, delete the deprecated code paths and database columns:
- Drop `tier_classifier.py`, `company_classifier.py`, `careers_registry.py`, `alerts/`.
- Drop unused columns: `company_tier`, `funding_stage`, `headcount_band`, `applied` (after R2 grace period).
- Collapse `cost_guardrails` to a single per-day call cap (kill the tier/per-company sub-rules).
- Trim `/api/health` and remove `/api/debug/*` endpoints from production.
- One Alembic migration captures all of this.

**Open questions**
- Anything you want to keep "just in case"? (Default: aggressive removal — git remembers everything.)

---

## What's Out of Scope (intentionally)

- Multi-user support / auth beyond the existing `X-API-Key`.
- Auto-sending messages (we draft + copy; the user sends manually — keeps you out of LinkedIn jail).
- Email-tracking / open-rate analytics.
- Calendar/interview scheduling.
- Resume tailoring per job (could be a future phase; not in this plan).

---

## Decisions I Need Before Phase R1

1. **Light only or light/dark toggle?** I recommend light-only.
2. **Accent color preference?** Slate-blue, muted teal, warm gray, or pick from 3 mockups?
3. **Hard delete vs. soft (`status=hidden`)?** I recommend soft.
4. **Happenstance access?** API token, CSV export, or "I'll figure it out by Phase R4"?

Once these four are answered, we can start Phase R1.
