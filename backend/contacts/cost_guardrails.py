"""
Cost guardrails for contact enrichment.

Two checks, in order of cost:
    1. Cache-hit short-circuit — if fresh contacts exist for the company,
       skip the provider call entirely and reuse them.
    2. Daily cap — total enrichments in the last 24h across all providers.

Guardrails are advisory — they return a decision + reason but don't
mutate state. The pipeline decides whether to honor them.
"""

from __future__ import annotations

from dataclasses import dataclass
from sqlalchemy.orm import Session

from backend.config import (
    CONTACT_CACHE_TTL_DAYS,
    CONTACT_ENRICHMENT_DAILY_CAP,
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
        cache_ttl_days: int | None = None,
    ):
        self.session = session
        self.daily_cap = (
            daily_cap if daily_cap is not None else CONTACT_ENRICHMENT_DAILY_CAP
        )
        self.cache_ttl_days = (
            cache_ttl_days
            if cache_ttl_days is not None
            else CONTACT_CACHE_TTL_DAYS
        )

    def check(self, company: str) -> GuardrailDecision:
        """
        Check guardrails for a given company.

        Order:
          1. cache-hit short-circuit (cheapest)
          2. daily cap (COUNT query — run last)
        """
        cached = get_contacts_for_company(
            self.session, company, max_age_days=self.cache_ttl_days,
        )
        if cached:
            return GuardrailDecision(
                allowed=False,
                reason=f"cache-hit:{len(cached)}-contacts-within-{self.cache_ttl_days}d",
                cached_contacts=len(cached),
            )

        enriched_today = count_recent_enrichments(self.session, within_hours=24)
        if enriched_today >= self.daily_cap:
            return GuardrailDecision(
                allowed=False,
                reason=f"daily-cap:{enriched_today}>={self.daily_cap}",
            )

        return GuardrailDecision(allowed=True, reason="ok")
