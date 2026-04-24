"""
Cost guardrails for contact enrichment.

Two independent caps:
    1. Daily cap   — total enrichments in the last 24h across all providers.
    2. Per-company — avoid re-hitting the same company when cache is fresh
                     OR when we've already pulled `per_company_cap` contacts.

Guardrails are advisory — they return a decision + reason but don't
mutate state. The pipeline decides whether to honor them. Keeping the
side-effects one level up makes the decision logic trivially testable.
"""

from __future__ import annotations

from dataclasses import dataclass
from sqlalchemy.orm import Session

from backend.config import (
    CONTACT_CACHE_TTL_DAYS,
    CONTACT_ENRICHMENT_DAILY_CAP,
    CONTACT_ENRICHMENT_PER_COMPANY_CAP,
)
from backend.database.crud import (
    count_recent_enrichments,
    get_contacts_for_company,
)


@dataclass
class GuardrailDecision:
    """Result of a guardrail check."""
    allowed: bool
    reason: str                 # short, human-readable
    cached_contacts: int = 0    # how many fresh cache hits we'd reuse


class ContactGuardrails:
    """
    Per-request budget enforcer.

    Daily cap is checked against Contact.last_enriched_at, so a cached hit
    (upsert that doesn't call the provider) also counts — which is a
    reasonable proxy since the pipeline only refreshes cache entries that
    the caller actually needed.
    """

    def __init__(
        self,
        session: Session,
        *,
        daily_cap: int | None = None,
        per_company_cap: int | None = None,
        cache_ttl_days: int | None = None,
    ):
        self.session = session
        self.daily_cap = (
            daily_cap if daily_cap is not None else CONTACT_ENRICHMENT_DAILY_CAP
        )
        self.per_company_cap = (
            per_company_cap
            if per_company_cap is not None
            else CONTACT_ENRICHMENT_PER_COMPANY_CAP
        )
        self.cache_ttl_days = (
            cache_ttl_days
            if cache_ttl_days is not None
            else CONTACT_CACHE_TTL_DAYS
        )

    def check(self, company: str) -> GuardrailDecision:
        """
        Check all guardrails for a given company. Returns the first failing
        decision, or an "allowed" decision if all pass.

        Order matters:
          1. cache-hit short-circuit (cheapest, fastest)
          2. per-company cap
          3. daily cap (requires a COUNT query — run last)
        """
        # (1) Fresh cache — skip the provider call entirely.
        cached = get_contacts_for_company(
            self.session, company, max_age_days=self.cache_ttl_days,
        )
        if cached:
            # We've got fresh data; we don't need to call the provider again.
            # `allowed=False` here means "don't spend a credit"; the pipeline
            # will return the cached contacts instead.
            return GuardrailDecision(
                allowed=False,
                reason=f"cache-hit:{len(cached)}-contacts-within-{self.cache_ttl_days}d",
                cached_contacts=len(cached),
            )

        # (2) Per-company cap — even stale cache entries count as "already
        # spent" for this company. Guards against a bug that would re-spend
        # on the same company once cache expires.
        total_for_company = len(get_contacts_for_company(self.session, company))
        if total_for_company >= self.per_company_cap:
            return GuardrailDecision(
                allowed=False,
                reason=(
                    f"per-company-cap:{total_for_company}>={self.per_company_cap}"
                ),
                cached_contacts=total_for_company,
            )

        # (3) Daily cap — protect overall spend.
        enriched_today = count_recent_enrichments(self.session, within_hours=24)
        if enriched_today >= self.daily_cap:
            return GuardrailDecision(
                allowed=False,
                reason=f"daily-cap:{enriched_today}>={self.daily_cap}",
            )

        return GuardrailDecision(allowed=True, reason="ok")
