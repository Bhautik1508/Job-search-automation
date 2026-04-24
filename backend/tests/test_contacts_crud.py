"""
Unit tests for Contact / JobContact CRUD helpers (Phase 7).
Uses an in-memory SQLite DB for isolation.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.models import Base, Contact, Job
from backend.database.crud import (
    count_recent_enrichments,
    get_contacts_for_company,
    get_contacts_for_job,
    link_job_to_contact,
    upsert_contact,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed_job(session, company: str = "Razorpay", title: str = "Product Manager") -> Job:
    job = Job(
        title=title,
        company=company,
        location="Bangalore",
        source_portal="naukri",
        source_engine="test",
        dedup_hash=f"hash_{title}_{company}",
        date_scraped=datetime.now(timezone.utc),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


class TestUpsertContact:
    def test_insert_new_contact(self, db_session):
        c = upsert_contact(
            db_session,
            name="Alice",
            company="Razorpay",
            role_type="hm",
            source_provider="apollo",
            title="Head of Product",
            linkedin_url="https://linkedin.com/in/alice",
            confidence=0.9,
        )
        assert c.id is not None
        assert c.name == "Alice"
        assert c.last_enriched_at is not None

    def test_upsert_refreshes_existing_by_linkedin(self, db_session):
        """Same linkedin_url → single row, updated fields."""
        first = upsert_contact(
            db_session,
            name="Alice",
            company="Razorpay",
            role_type="hm",
            source_provider="apollo",
            linkedin_url="https://linkedin.com/in/alice",
            title="PM",
        )
        first_enriched_at = first.last_enriched_at

        # Simulate time passing
        import time
        time.sleep(0.01)

        second = upsert_contact(
            db_session,
            name="Alice Smith",  # updated name
            company="Razorpay",
            role_type="hm",
            source_provider="apollo",
            linkedin_url="https://linkedin.com/in/alice",
            title="Head of Product",
            email="alice@razorpay.com",
        )

        assert second.id == first.id
        assert second.name == "Alice Smith"
        assert second.title == "Head of Product"
        assert second.email == "alice@razorpay.com"
        assert second.last_enriched_at >= first_enriched_at
        assert db_session.query(Contact).count() == 1

    def test_upsert_by_company_name_when_no_linkedin(self, db_session):
        """Without linkedin_url we dedupe on (company, name)."""
        upsert_contact(
            db_session,
            name="Bob",
            company="CRED",
            role_type="recruiter",
            source_provider="hunter",
            email="bob@cred.club",
        )
        upsert_contact(
            db_session,
            name="Bob",
            company="CRED",
            role_type="recruiter",
            source_provider="hunter",
            email="bob.new@cred.club",
        )
        rows = db_session.query(Contact).all()
        assert len(rows) == 1
        assert rows[0].email == "bob.new@cred.club"

    def test_same_name_different_company_creates_two(self, db_session):
        upsert_contact(
            db_session, name="Alice", company="Razorpay",
            role_type="hm", source_provider="apollo",
        )
        upsert_contact(
            db_session, name="Alice", company="PhonePe",
            role_type="hm", source_provider="apollo",
        )
        assert db_session.query(Contact).count() == 2


class TestLinkJobToContact:
    def test_create_link(self, db_session):
        job = _seed_job(db_session)
        c = upsert_contact(
            db_session, name="Alice", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="https://linkedin.com/in/alice",
        )
        link = link_job_to_contact(
            db_session, job_id=job.id, contact_id=c.id,
            provider="apollo", confidence=0.8,
        )
        assert link.id is not None
        assert link.job_id == job.id
        assert link.contact_id == c.id

    def test_link_idempotent(self, db_session):
        """Re-linking same (job, contact) updates the existing row."""
        job = _seed_job(db_session)
        c = upsert_contact(
            db_session, name="Alice", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="https://linkedin.com/in/alice",
        )
        link1 = link_job_to_contact(
            db_session, job_id=job.id, contact_id=c.id,
            provider="apollo", confidence=0.5,
        )
        link2 = link_job_to_contact(
            db_session, job_id=job.id, contact_id=c.id,
            provider="hunter", confidence=0.7,
        )
        assert link1.id == link2.id
        assert link2.provider == "hunter"
        assert link2.confidence == 0.7


class TestGetContactsForCompany:
    def test_filter_by_role_type(self, db_session):
        upsert_contact(
            db_session, name="HM1", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="u1",
        )
        upsert_contact(
            db_session, name="Recruiter1", company="Razorpay",
            role_type="recruiter", source_provider="apollo",
            linkedin_url="u2",
        )

        hms = get_contacts_for_company(db_session, "Razorpay", role_type="hm")
        recs = get_contacts_for_company(db_session, "Razorpay", role_type="recruiter")
        assert len(hms) == 1
        assert hms[0].name == "HM1"
        assert len(recs) == 1

    def test_case_insensitive_company(self, db_session):
        upsert_contact(
            db_session, name="Alice", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="u1",
        )
        found = get_contacts_for_company(db_session, "razorpay")
        assert len(found) == 1

    def test_cache_ttl_filter(self, db_session):
        """max_age_days drops contacts enriched before the cutoff."""
        stale = upsert_contact(
            db_session, name="StaleHM", company="CRED",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_stale",
        )
        # Manually age this row out
        stale.last_enriched_at = datetime.now(timezone.utc) - timedelta(days=45)
        db_session.commit()

        upsert_contact(
            db_session, name="FreshHM", company="CRED",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_fresh",
        )

        fresh_only = get_contacts_for_company(db_session, "CRED", max_age_days=30)
        assert len(fresh_only) == 1
        assert fresh_only[0].name == "FreshHM"

    def test_sorted_by_confidence_desc(self, db_session):
        upsert_contact(
            db_session, name="Lo", company="A",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_lo", confidence=0.3,
        )
        upsert_contact(
            db_session, name="Hi", company="A",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_hi", confidence=0.9,
        )
        results = get_contacts_for_company(db_session, "A")
        assert results[0].name == "Hi"
        assert results[1].name == "Lo"


class TestGetContactsForJob:
    def test_returns_pairs_sorted_by_link_confidence(self, db_session):
        job = _seed_job(db_session)
        low = upsert_contact(
            db_session, name="Low", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_low",
        )
        high = upsert_contact(
            db_session, name="High", company="Razorpay",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_high",
        )
        link_job_to_contact(
            db_session, job_id=job.id, contact_id=low.id,
            provider="apollo", confidence=0.4,
        )
        link_job_to_contact(
            db_session, job_id=job.id, contact_id=high.id,
            provider="apollo", confidence=0.9,
        )

        pairs = get_contacts_for_job(db_session, job.id)
        assert len(pairs) == 2
        assert pairs[0][0].name == "High"
        assert pairs[1][0].name == "Low"

    def test_empty_when_no_links(self, db_session):
        job = _seed_job(db_session)
        assert get_contacts_for_job(db_session, job.id) == []


class TestCountRecentEnrichments:
    def test_counts_within_window(self, db_session):
        # fresh
        upsert_contact(
            db_session, name="Fresh", company="X",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_fresh",
        )
        # stale
        stale = upsert_contact(
            db_session, name="Stale", company="Y",
            role_type="hm", source_provider="apollo",
            linkedin_url="u_stale",
        )
        stale.last_enriched_at = datetime.now(timezone.utc) - timedelta(hours=48)
        db_session.commit()

        assert count_recent_enrichments(db_session, within_hours=24) == 1
        assert count_recent_enrichments(db_session, within_hours=72) == 2
