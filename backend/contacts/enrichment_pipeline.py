"""
Contact enrichment pipeline — Phase 7.

Flow:
    for job in eligible jobs:
        decision = guardrails.check(company)
        if decision.allowed:
            fetch from Apollo → upsert contacts
        else if decision.cached_contacts:
            reuse cached contacts
        link each contact to the job

Eligibility:
    - verdict at or above CONTACT_ENRICHMENT_MIN_VERDICT
    - company_tier in CONTACT_ENRICHMENT_ELIGIBLE_TIERS
    - job.applied is False (we don't enrich already-applied jobs)

This keeps Apollo spend focused on high-quality, still-actionable jobs.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable

from sqlalchemy.orm import Session

from backend.config import (
    CONTACT_ENRICHMENT_ELIGIBLE_TIERS,
    CONTACT_ENRICHMENT_MIN_VERDICT,
)
from backend.contacts.apollo_client import ApolloClient, ApolloContact, iter_unique_contacts
from backend.contacts.apify_linkedin_client import ApifyLinkedInClient
from backend.contacts.cost_guardrails import ContactGuardrails
from backend.contacts.hunter_client import HunterClient
from backend.database.crud import (
    get_contacts_for_company,
    link_job_to_contact,
    upsert_contact,
)
from backend.database.models import Contact, Job


# Verdict rank — numeric comparison against CONTACT_ENRICHMENT_MIN_VERDICT.
_VERDICT_RANK = {
    "SKIP": 0,
    "WEAK_FIT": 1,
    "MAYBE_FIT": 2,
    "GOOD_FIT": 3,
    "STRONG_FIT": 4,
}


@dataclass
class EnrichmentResult:
    """Summary of a single enrichment run."""
    jobs_considered: int = 0
    jobs_eligible: int = 0
    jobs_enriched: int = 0
    jobs_skipped: int = 0
    contacts_created: int = 0
    contacts_reused_from_cache: int = 0
    links_created: int = 0
    skip_reasons: dict[str, int] = field(default_factory=dict)
    provider_errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "jobs_considered": self.jobs_considered,
            "jobs_eligible": self.jobs_eligible,
            "jobs_enriched": self.jobs_enriched,
            "jobs_skipped": self.jobs_skipped,
            "contacts_created": self.contacts_created,
            "contacts_reused_from_cache": self.contacts_reused_from_cache,
            "links_created": self.links_created,
            "skip_reasons": dict(self.skip_reasons),
            "provider_errors": list(self.provider_errors),
        }


class EnrichmentPipeline:
    """
    Orchestrator for contact enrichment.

    Dependencies are injected (client, guardrails) so tests can supply
    stubs without monkey-patching module globals.
    """

    def __init__(
        self,
        session: Session,
        *,
        client: ApolloClient | None = None,
        hunter_client: HunterClient | None = None,
        linkedin_client: ApifyLinkedInClient | None = None,
        guardrails: ContactGuardrails | None = None,
        eligible_tiers: Iterable[str] | None = None,
        min_verdict: str | None = None,
    ):
        self.session = session
        self.client = client or ApolloClient()
        self.hunter_client = hunter_client or HunterClient()
        self.linkedin_client = linkedin_client or ApifyLinkedInClient()
        self.guardrails = guardrails or ContactGuardrails(session)
        self.eligible_tiers = set(
            eligible_tiers
            if eligible_tiers is not None
            else CONTACT_ENRICHMENT_ELIGIBLE_TIERS
        )
        self.min_verdict = (min_verdict or CONTACT_ENRICHMENT_MIN_VERDICT).upper()
        self._min_verdict_rank = _VERDICT_RANK.get(self.min_verdict, 3)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run(self, jobs: Iterable[Job], *, skip_eligibility: bool = False) -> EnrichmentResult:
        """Enrich the given iterable of jobs.

        When ``skip_eligibility`` is True, the verdict/tier/applied gates are
        bypassed — used by the per-job admin path where the caller has
        already decided this job is worth the credits.
        """
        result = EnrichmentResult()
        for job in jobs:
            result.jobs_considered += 1
            reason = None if skip_eligibility else self._ineligible_reason(job)
            if reason:
                result.jobs_skipped += 1
                result.skip_reasons[reason] = result.skip_reasons.get(reason, 0) + 1
                continue

            result.jobs_eligible += 1
            self._enrich_one(job, result)
        return result

    def enrich_job(self, job: Job, *, skip_eligibility: bool = True) -> EnrichmentResult:
        """Enrich a single job — convenience wrapper used by the API.

        Defaults to ``skip_eligibility=True`` because callers reach this path
        by manually clicking a specific job; the eligibility gates are batch
        controls and shouldn't fight a deliberate user action.
        """
        return self.run([job], skip_eligibility=skip_eligibility)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _ineligible_reason(self, job: Job) -> str | None:
        """Return None when eligible, else a short skip-reason key."""
        if job.applied:
            return "already_applied"

        if not job.verdict:
            return "unscored"
        rank = _VERDICT_RANK.get(job.verdict.upper(), -1)
        if rank < self._min_verdict_rank:
            return f"verdict_below_{self.min_verdict.lower()}"

        # Tier gate: skip when config specifies eligible tiers AND the job's
        # tier isn't in the list. An empty config means "no tier gate".
        if self.eligible_tiers and job.company_tier not in self.eligible_tiers:
            return "tier_not_eligible"
        return None

    def _enrich_one(self, job: Job, result: EnrichmentResult) -> None:
        """Fetch-or-cache contacts for a single job and link them."""
        decision = self.guardrails.check(job.company)

        # Not-allowed with cached_contacts>0 → reuse cache path.
        # Not-allowed with cached_contacts==0 → hard skip (daily cap exceeded).
        if not decision.allowed:
            if decision.cached_contacts:
                cached = get_contacts_for_company(self.session, job.company)
                result.contacts_reused_from_cache += len(cached)
                for contact in cached:
                    link_job_to_contact(
                        self.session,
                        job_id=job.id,
                        contact_id=contact.id,
                        provider=contact.source_provider,
                        confidence=contact.confidence,
                    )
                    result.links_created += 1
                result.jobs_enriched += 1
                return

            result.jobs_skipped += 1
            result.skip_reasons[decision.reason] = (
                result.skip_reasons.get(decision.reason, 0) + 1
            )
            return

        # Guardrails said "go" — try providers in priority order.
        # 1) Apollo (primary) → 2) Apify LinkedIn → 3) Hunter (email patterns).
        # Stop at the first provider that returns any results.
        fetched, provider_used = self._run_providers(job, result)

        if not fetched:
            result.jobs_skipped += 1
            if not provider_used:
                result.skip_reasons["no_provider_configured"] = (
                    result.skip_reasons.get("no_provider_configured", 0) + 1
                )
            else:
                result.skip_reasons["provider_returned_zero"] = (
                    result.skip_reasons.get("provider_returned_zero", 0) + 1
                )
            return

        unique = iter_unique_contacts(fetched)
        contacts_for_job = self._persist_contacts(unique, provider=provider_used)
        for contact in contacts_for_job:
            link_job_to_contact(
                self.session,
                job_id=job.id,
                contact_id=contact.id,
                provider=provider_used,
                confidence=contact.confidence,
            )
            result.links_created += 1
        result.contacts_created += len(contacts_for_job)
        result.jobs_enriched += 1

    def _run_providers(
        self, job: Job, result: EnrichmentResult
    ) -> tuple[list[ApolloContact], str | None]:
        """
        Walk Apollo → Apify LinkedIn → Hunter. Return at the first provider
        that yields ≥1 contact. Exceptions from one provider are captured
        on the result and we continue to the next — the whole point of
        fallbacks is to cover each one's weaknesses.
        """
        providers: list[tuple[str, object]] = [
            ("apollo", self.client),
            ("linkedin_apify", self.linkedin_client),
            ("hunter", self.hunter_client),
        ]

        any_configured = False
        for name, provider in providers:
            if not getattr(provider, "is_configured", False):
                result.skip_reasons[f"{name}_not_configured"] = (
                    result.skip_reasons.get(f"{name}_not_configured", 0) + 1
                )
                continue
            any_configured = True

            try:
                # Pass job_title so providers that support role-aware search
                # (Apollo) can filter HM keywords to the job's discipline
                # instead of always searching for product titles. Providers
                # that don't accept the kwarg ignore it.
                try:
                    fetched = provider.search_people_at_company(
                        job.company, job_title=job.title,
                    )
                except TypeError:
                    fetched = provider.search_people_at_company(job.company)
            except Exception as e:  # noqa: BLE001 — log & fall through
                result.provider_errors.append(
                    f"{job.company} via {name}: {type(e).__name__}: {e}"
                )
                continue

            if fetched:
                return fetched, name

        return [], ("fallback_exhausted" if any_configured else None)

    def _persist_contacts(
        self,
        contacts: Iterable[ApolloContact],
        *,
        provider: str,
    ) -> list[Contact]:
        """Upsert each contact, returning the persisted Contact rows."""
        out: list[Contact] = []
        for c in contacts:
            persisted = upsert_contact(
                self.session,
                name=c.name,
                title=c.title,
                company=c.company,
                linkedin_url=c.linkedin_url,
                email=c.email,
                role_type=c.role_type,
                confidence=c.confidence,
                source_provider=provider,
                raw_payload=json.dumps(c.raw) if c.raw else None,
            )
            out.append(persisted)
        return out
