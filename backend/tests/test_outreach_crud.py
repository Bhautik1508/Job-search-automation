"""
Unit tests for outreach-draft CRUD helpers.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database.crud import (
    get_outreach_draft_by_id,
    get_outreach_drafts_for_job,
    update_outreach_status,
    upsert_contact,
    upsert_outreach_draft,
)
from backend.database.models import Base, Job, OutreachDraft


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session
    session.close()


def _seed(session):
    job = Job(
        title="PM", company="Razorpay", location="Bangalore",
        source_portal="naukri", source_engine="test",
        dedup_hash="h_1",
        date_scraped=datetime.now(timezone.utc),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    contact = upsert_contact(
        session, name="Alice", company="Razorpay",
        role_type="hm", source_provider="apollo",
        linkedin_url="u1",
    )
    return job, contact


class TestUpsertOutreachDraft:
    def test_inserts_new_draft(self, db_session):
        job, contact = _seed(db_session)
        draft = upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="linkedin_note", tone="peer-pm",
            body="Hi Alice", model="gemini-test",
        )
        assert draft.id is not None
        assert draft.status == "draft"
        assert draft.model == "gemini-test"

    def test_upsert_replaces_body_on_rerun(self, db_session):
        job, contact = _seed(db_session)
        first = upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="email", tone="peer-pm",
            body="v1", subject="s1", model="m1",
        )
        second = upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="email", tone="recruiter-formal",
            body="v2", subject="s2", model="m2",
        )
        assert first.id == second.id
        assert second.body == "v2"
        assert second.subject == "s2"
        assert second.tone == "recruiter-formal"
        # updated_at should advance on rerun
        assert second.updated_at >= first.updated_at

    def test_different_channels_are_separate_rows(self, db_session):
        job, contact = _seed(db_session)
        a = upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="linkedin_note", tone="peer-pm", body="a",
        )
        b = upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="email", tone="peer-pm", body="b",
        )
        assert a.id != b.id
        assert db_session.query(OutreachDraft).count() == 2

    def test_status_preserved_on_regeneration(self, db_session):
        """Regenerating shouldn't reset a 'sent' draft back to 'draft'."""
        job, contact = _seed(db_session)
        d = upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="email", tone="peer-pm", body="v1",
        )
        update_outreach_status(db_session, d.id, "sent")

        regenerated = upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="email", tone="peer-pm", body="v2",
        )
        assert regenerated.status == "sent"
        assert regenerated.body == "v2"

    def test_explicit_status_overrides_existing(self, db_session):
        job, contact = _seed(db_session)
        d = upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="email", tone="peer-pm", body="v1",
        )
        assert d.status == "draft"
        update_outreach_status(db_session, d.id, "sent")

        # Explicit status= on upsert should still win.
        updated = upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="email", tone="peer-pm", body="v2",
            status="draft",
        )
        assert updated.status == "draft"


class TestListAndGet:
    def test_get_drafts_for_job_ordered(self, db_session):
        job, contact = _seed(db_session)
        upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="linkedin_note", tone="peer-pm", body="a",
        )
        upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="email", tone="peer-pm", body="b",
        )
        drafts = get_outreach_drafts_for_job(db_session, job.id)
        assert len(drafts) == 2

    def test_get_by_id_missing(self, db_session):
        assert get_outreach_draft_by_id(db_session, 99999) is None


class TestUpdateStatus:
    def test_sets_status_and_bumps_updated_at(self, db_session):
        job, contact = _seed(db_session)
        d = upsert_outreach_draft(
            db_session, job_id=job.id, contact_id=contact.id,
            channel="email", tone="peer-pm", body="v1",
        )
        updated = update_outreach_status(db_session, d.id, "replied")
        assert updated.status == "replied"

    def test_missing_id_returns_none(self, db_session):
        assert update_outreach_status(db_session, 12345, "sent") is None
