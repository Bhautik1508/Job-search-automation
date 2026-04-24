"""
Unit tests for the per-company + daily cost guardrails.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base, Contact
from backend.database.crud import upsert_contact
from backend.contacts.cost_guardrails import ContactGuardrails


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _age_contact(session, contact: Contact, days: int) -> None:
    """Backdate last_enriched_at so the cache-TTL check treats it as stale."""
    contact.last_enriched_at = datetime.now(timezone.utc) - timedelta(days=days)
    session.commit()


class TestCacheHitShortCircuit:
    def test_fresh_cache_blocks_provider_call(self, db_session):
        upsert_contact(
            db_session, name="Alice", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="u1",
        )
        g = ContactGuardrails(
            db_session, daily_cap=100, per_company_cap=5, cache_ttl_days=30,
        )
        decision = g.check("Razorpay")
        assert decision.allowed is False
        assert decision.cached_contacts == 1
        assert "cache-hit" in decision.reason

    def test_stale_cache_does_not_short_circuit(self, db_session):
        c = upsert_contact(
            db_session, name="Alice", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="u1",
        )
        _age_contact(db_session, c, days=45)

        g = ContactGuardrails(
            db_session, daily_cap=100, per_company_cap=5, cache_ttl_days=30,
        )
        decision = g.check("Razorpay")
        # Stale cache hit → not a cache short-circuit, but per-company cap
        # still sees the row (1 < 5), so we proceed.
        assert decision.allowed is True


class TestPerCompanyCap:
    def test_cap_hit_blocks_call(self, db_session):
        for i in range(3):
            c = upsert_contact(
                db_session,
                name=f"C{i}", company="Razorpay",
                role_type="hm", source_provider="apollo",
                linkedin_url=f"u{i}",
            )
            _age_contact(db_session, c, days=90)  # stale so cache doesn't short-circuit

        g = ContactGuardrails(
            db_session, daily_cap=100, per_company_cap=3, cache_ttl_days=30,
        )
        decision = g.check("Razorpay")
        assert decision.allowed is False
        assert "per-company-cap" in decision.reason

    def test_below_cap_allows(self, db_session):
        c = upsert_contact(
            db_session, name="C0", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="u0",
        )
        _age_contact(db_session, c, days=90)

        g = ContactGuardrails(
            db_session, daily_cap=100, per_company_cap=3, cache_ttl_days=30,
        )
        assert g.check("Razorpay").allowed is True


class TestDailyCap:
    def test_daily_cap_blocks(self, db_session):
        # 5 recent enrichments at 5 different companies (so per-company cap doesn't trigger)
        for i in range(5):
            upsert_contact(
                db_session,
                name=f"C{i}", company=f"Co{i}",
                role_type="hm", source_provider="apollo",
                linkedin_url=f"u{i}",
            )

        g = ContactGuardrails(
            db_session, daily_cap=5, per_company_cap=10, cache_ttl_days=30,
        )
        # New company → no cache hit, no per-company history; daily cap should bite.
        decision = g.check("NewCo")
        assert decision.allowed is False
        assert "daily-cap" in decision.reason

    def test_old_enrichments_dont_count_against_daily(self, db_session):
        c = upsert_contact(
            db_session, name="X", company="OldCo",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_old",
        )
        # Push it past 24h
        c.last_enriched_at = datetime.now(timezone.utc) - timedelta(hours=30)
        db_session.commit()

        g = ContactGuardrails(
            db_session, daily_cap=1, per_company_cap=5, cache_ttl_days=30,
        )
        decision = g.check("FreshCo")
        assert decision.allowed is True

    def test_daily_cap_ordering_after_cache_hit(self, db_session):
        """A cache-hit on the target company should short-circuit BEFORE daily cap check."""
        upsert_contact(
            db_session, name="Fresh", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_fresh",
        )
        g = ContactGuardrails(
            db_session, daily_cap=0, per_company_cap=5, cache_ttl_days=30,
        )
        decision = g.check("Razorpay")
        assert decision.cached_contacts == 1
        assert "cache-hit" in decision.reason
