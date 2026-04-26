"""
Unit tests for the end-to-end contact enrichment pipeline.

Uses a fake ApolloClient so no network call is ever made.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base, Contact, Job, JobContact
from backend.database.crud import upsert_contact
from backend.contacts.apollo_client import ApolloContact
from backend.contacts.cost_guardrails import ContactGuardrails
from backend.contacts.enrichment_pipeline import EnrichmentPipeline


class _FakeApollo:
    """Deterministic stand-in for ApolloClient."""

    def __init__(
        self,
        *,
        is_configured: bool = True,
        contacts: list[ApolloContact] | None = None,
        raise_exc: Exception | None = None,
    ):
        self.is_configured = is_configured
        self._contacts = contacts or []
        self._raise = raise_exc
        self.calls: list[str] = []

    def search_people_at_company(self, company: str, *, per_page: int = 5):
        self.calls.append(company)
        if self._raise:
            raise self._raise
        return list(self._contacts)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _job(
    session,
    *,
    company: str = "Razorpay",
    verdict: str = "GOOD_FIT",
    status: str = "new",
    title: str = "Product Manager",
    hash_suffix: str = "",
) -> Job:
    job = Job(
        title=title,
        company=company,
        location="Bangalore",
        source_portal="naukri",
        source_engine="test",
        verdict=verdict,
        status=status,
        dedup_hash=f"hash_{company}_{verdict}_{title}_{hash_suffix}",
        date_scraped=datetime.now(timezone.utc),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _apollo_contact(
    name: str = "Alice",
    company: str = "Razorpay",
    role_type: str = "hm",
    linkedin_url: str | None = "https://linkedin.com/in/alice",
) -> ApolloContact:
    return ApolloContact(
        name=name,
        title="Head of Product" if role_type == "hm" else "Recruiter",
        company=company,
        linkedin_url=linkedin_url,
        email=None,
        role_type=role_type,
        confidence=0.9,
        raw={"fake": True},
    )


class TestEligibility:
    def test_skips_applied_jobs(self, db_session):
        job = _job(db_session, status="applied")
        fake = _FakeApollo(contacts=[_apollo_contact()])
        pipe = EnrichmentPipeline(db_session, client=fake)

        result = pipe.run([job])
        assert result.jobs_skipped == 1
        assert result.skip_reasons.get("status_applied") == 1
        assert fake.calls == []

    def test_skips_unscored_jobs(self, db_session):
        job = _job(db_session, verdict=None)
        fake = _FakeApollo(contacts=[_apollo_contact()])
        pipe = EnrichmentPipeline(db_session, client=fake)

        result = pipe.run([job])
        assert result.skip_reasons.get("unscored") == 1
        assert fake.calls == []

    def test_skips_weak_verdict(self, db_session):
        job = _job(db_session, verdict="WEAK_FIT")
        fake = _FakeApollo(contacts=[_apollo_contact()])
        pipe = EnrichmentPipeline(db_session, client=fake, min_verdict="GOOD_FIT")

        result = pipe.run([job])
        assert any(
            k.startswith("verdict_below") for k in result.skip_reasons
        )
        assert fake.calls == []

    def test_skips_hidden_jobs(self, db_session):
        job = _job(db_session, status="hidden")
        fake = _FakeApollo(contacts=[_apollo_contact()])
        pipe = EnrichmentPipeline(db_session, client=fake)

        result = pipe.run([job])
        assert result.skip_reasons.get("status_hidden") == 1
        assert fake.calls == []


class TestHappyPath:
    def test_enriches_eligible_job(self, db_session):
        job = _job(db_session)
        fake = _FakeApollo(contacts=[
            _apollo_contact("Alice", role_type="hm", linkedin_url="u_alice"),
            _apollo_contact("Bob", role_type="recruiter", linkedin_url="u_bob"),
        ])
        pipe = EnrichmentPipeline(db_session, client=fake)

        result = pipe.run([job])
        assert result.jobs_eligible == 1
        assert result.jobs_enriched == 1
        assert result.contacts_created == 2
        assert result.links_created == 2
        assert fake.calls == ["Razorpay"]

        contacts = db_session.query(Contact).all()
        assert {c.name for c in contacts} == {"Alice", "Bob"}

        links = db_session.query(JobContact).all()
        assert len(links) == 2
        for link in links:
            assert link.job_id == job.id
            assert link.provider == "apollo"

    def test_dedupes_duplicate_contacts_in_batch(self, db_session):
        job = _job(db_session)
        fake = _FakeApollo(contacts=[
            _apollo_contact("Alice", linkedin_url="u_alice"),
            _apollo_contact("Alice 2", linkedin_url="u_alice"),  # same URL
        ])
        pipe = EnrichmentPipeline(db_session, client=fake)

        result = pipe.run([job])
        assert result.contacts_created == 1


class TestCacheReuse:
    def test_reuses_fresh_cache_without_calling_apollo(self, db_session):
        job = _job(db_session, company="Razorpay", hash_suffix="a")

        # Seed cache: one fresh contact for Razorpay
        upsert_contact(
            db_session, name="Cached Alice", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_cached",
        )

        fake = _FakeApollo(contacts=[_apollo_contact()])
        pipe = EnrichmentPipeline(db_session, client=fake)

        result = pipe.run([job])
        assert fake.calls == []  # cache short-circuited
        assert result.contacts_reused_from_cache == 1
        assert result.links_created == 1
        assert result.contacts_created == 0


class TestGuardrailPaths:
    def test_daily_cap_skip_records_reason(self, db_session):
        """
        Daily cap of 0 + brand-new company → guardrails deny, no cached
        contacts, so the pipeline records a skip rather than reusing cache.
        """
        job = _job(db_session)
        guardrails = ContactGuardrails(
            db_session, daily_cap=0, cache_ttl_days=30,
        )
        fake = _FakeApollo(contacts=[_apollo_contact()])
        pipe = EnrichmentPipeline(db_session, client=fake, guardrails=guardrails)

        result = pipe.run([job])
        assert fake.calls == []
        assert result.jobs_skipped == 1
        # skip_reasons key is the guardrail decision string
        assert any("daily-cap" in k for k in result.skip_reasons)


class TestFailureModes:
    def test_apollo_not_configured(self, db_session):
        job = _job(db_session)
        fake = _FakeApollo(is_configured=False)
        pipe = EnrichmentPipeline(db_session, client=fake)

        result = pipe.run([job])
        assert result.jobs_skipped == 1
        assert "apollo_not_configured" in result.skip_reasons
        assert fake.calls == []  # guardrails allowed, but client.is_configured=False gates

    def test_provider_returns_zero(self, db_session):
        job = _job(db_session)
        fake = _FakeApollo(contacts=[])
        pipe = EnrichmentPipeline(db_session, client=fake)

        result = pipe.run([job])
        assert result.skip_reasons.get("provider_returned_zero") == 1
        assert result.contacts_created == 0

    def test_provider_exception_is_captured_not_raised(self, db_session):
        job = _job(db_session)
        fake = _FakeApollo(raise_exc=RuntimeError("boom"))
        pipe = EnrichmentPipeline(db_session, client=fake)

        # Pipeline should NOT raise — it should record the error and continue.
        result = pipe.run([job])
        assert result.provider_errors
        assert "Razorpay via apollo: RuntimeError: boom" in result.provider_errors[0]


class _FakeProvider:
    """
    Minimal shim that mimics ApolloClient's surface so it can be
    dependency-injected into EnrichmentPipeline as a fallback.
    """
    def __init__(self, *, is_configured=True, contacts=None, raise_exc=None):
        self.is_configured = is_configured
        self._contacts = contacts or []
        self._raise = raise_exc
        self.calls: list[str] = []

    def search_people_at_company(self, company, **_kw):
        self.calls.append(company)
        if self._raise:
            raise self._raise
        return list(self._contacts)


class TestFallbackChain:
    """Apollo → Apify LinkedIn → Hunter — stop at first provider with hits."""

    def test_falls_through_to_linkedin_when_apollo_empty(self, db_session):
        job = _job(db_session)
        apollo = _FakeApollo(contacts=[])  # configured but returns zero
        linkedin = _FakeProvider(contacts=[_apollo_contact("From LinkedIn", linkedin_url="u_li")])
        hunter = _FakeProvider(contacts=[])

        pipe = EnrichmentPipeline(
            db_session, client=apollo, linkedin_client=linkedin, hunter_client=hunter,
        )
        result = pipe.run([job])

        assert result.contacts_created == 1
        assert result.jobs_enriched == 1
        contact = db_session.query(Contact).one()
        assert contact.name == "From LinkedIn"
        # Link should be tagged with the fallback provider name, not "apollo".
        link = db_session.query(JobContact).one()
        assert link.provider == "linkedin_apify"
        # Hunter should NOT have been called — we stopped at LinkedIn.
        assert hunter.calls == []

    def test_falls_through_to_hunter_when_apollo_and_linkedin_empty(self, db_session):
        job = _job(db_session)
        apollo = _FakeApollo(contacts=[])
        linkedin = _FakeProvider(contacts=[])
        hunter = _FakeProvider(contacts=[_apollo_contact("From Hunter", linkedin_url=None)])

        pipe = EnrichmentPipeline(
            db_session, client=apollo, linkedin_client=linkedin, hunter_client=hunter,
        )
        result = pipe.run([job])

        assert result.contacts_created == 1
        link = db_session.query(JobContact).one()
        assert link.provider == "hunter"

    def test_apollo_exception_triggers_fallback(self, db_session):
        job = _job(db_session)
        apollo = _FakeApollo(raise_exc=RuntimeError("apollo down"))
        linkedin = _FakeProvider(contacts=[_apollo_contact("LI Alice", linkedin_url="u_li")])
        hunter = _FakeProvider(contacts=[])

        pipe = EnrichmentPipeline(
            db_session, client=apollo, linkedin_client=linkedin, hunter_client=hunter,
        )
        result = pipe.run([job])

        assert any("via apollo" in e for e in result.provider_errors)
        assert result.contacts_created == 1
        link = db_session.query(JobContact).one()
        assert link.provider == "linkedin_apify"

    def test_all_providers_unconfigured_records_no_provider(self, db_session):
        job = _job(db_session)
        apollo = _FakeApollo(is_configured=False)
        linkedin = _FakeProvider(is_configured=False)
        hunter = _FakeProvider(is_configured=False)

        pipe = EnrichmentPipeline(
            db_session, client=apollo, linkedin_client=linkedin, hunter_client=hunter,
        )
        result = pipe.run([job])

        assert result.jobs_skipped == 1
        assert result.skip_reasons.get("no_provider_configured") == 1
        assert "apollo_not_configured" in result.skip_reasons
        assert "linkedin_apify_not_configured" in result.skip_reasons
        assert "hunter_not_configured" in result.skip_reasons


class TestEnrichJobHelper:
    def test_enrich_job_is_single_job_wrapper(self, db_session):
        job = _job(db_session)
        fake = _FakeApollo(contacts=[_apollo_contact()])
        pipe = EnrichmentPipeline(db_session, client=fake)

        result = pipe.enrich_job(job)
        assert result.jobs_considered == 1
        assert result.jobs_enriched == 1

    def test_enrich_job_bypasses_eligibility_by_default(self, db_session):
        """Manual per-job calls should ignore status/verdict gates."""
        job = _job(db_session, verdict=None, status="applied")
        fake = _FakeApollo(contacts=[_apollo_contact()])
        pipe = EnrichmentPipeline(db_session, client=fake)

        result = pipe.enrich_job(job)
        assert result.jobs_enriched == 1
        assert result.skip_reasons == {}

    def test_enrich_job_with_eligibility_still_skips(self, db_session):
        job = _job(db_session, verdict=None)
        pipe = EnrichmentPipeline(db_session, client=_FakeApollo())
        result = pipe.enrich_job(job, skip_eligibility=False)
        assert result.jobs_skipped == 1
        assert "unscored" in result.skip_reasons
