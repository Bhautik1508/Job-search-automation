"""
API tests for Phase 8 outreach endpoints.

We patch OutreachGenerator so the route exercises validation + persistence
without calling Gemini.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.main import app
import backend.api.main as api_main
from backend.database.crud import upsert_contact, upsert_outreach_draft
from backend.database.models import Base, Job
from backend.outreach.generator import GenerationResult


_test_engine = create_engine(
    "sqlite:///:memory:",
    echo=False,
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
Base.metadata.create_all(_test_engine)
_TestSession = sessionmaker(bind=_test_engine)


@pytest.fixture(autouse=True)
def _patch_db():
    api_main._engine = _test_engine
    api_main._SessionFactory = _TestSession
    yield
    session = _TestSession()
    try:
        session.execute(text("DELETE FROM outreach_drafts"))
        session.execute(text("DELETE FROM job_contacts"))
        session.execute(text("DELETE FROM contacts"))
        session.execute(text("DELETE FROM jobs"))
        session.execute(text("DELETE FROM scrape_scans"))
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(app)


def _seed(session):
    job = Job(
        title="Product Manager",
        company="Razorpay",
        location="Bangalore",
        source_portal="naukri",
        source_engine="test",
        dedup_hash=f"h_{datetime.now(timezone.utc).timestamp()}",
        date_scraped=datetime.now(timezone.utc),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    contact = upsert_contact(
        session, name="Alice", company="Razorpay",
        role_type="hm", source_provider="apollo", linkedin_url="u1",
    )
    return job, contact


def _install_fake_generator(monkeypatch, *, configured=True, result=None):
    """Replace the generator class referenced inside the endpoint."""
    import backend.outreach.generator as gen_module

    canned_result = result or GenerationResult(
        subject=None,
        body="Hi Alice, I'd love to chat about the PM role at Razorpay.",
        portfolio_ids_used=["upi-onboarding"],
        model="gemini-test",
        channel="linkedin_note",
        tone="peer-pm",
    )

    class _StubGenerator:
        def __init__(self, *a, **kw):
            self.is_configured = configured

        def generate(self, *, job, contact, channel, tone, **kw):
            # Mirror the real generator's channel/tone validation.
            from backend.outreach.generator import CHANNELS, TONES
            if channel not in CHANNELS:
                raise ValueError(f"Unknown channel {channel!r}")
            if tone not in TONES:
                raise ValueError(f"Unknown tone {tone!r}")
            if not self.is_configured:
                return None
            return canned_result

    monkeypatch.setattr(gen_module, "OutreachGenerator", _StubGenerator)


class TestGenerateDraft:
    def test_creates_draft(self, client, monkeypatch):
        session = _TestSession()
        try:
            job, contact = _seed(session)
            job_id, contact_id = job.id, contact.id
        finally:
            session.close()

        _install_fake_generator(monkeypatch)

        resp = client.post("/api/outreach/draft", json={
            "job_id": job_id,
            "contact_id": contact_id,
            "channel": "linkedin_note",
            "tone": "peer-pm",
        })
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["contact_id"] == contact_id
        assert data["channel"] == "linkedin_note"
        assert "Alice" in data["body"]
        assert data["model"] == "gemini-test"

    def test_regenerating_is_idempotent(self, client, monkeypatch):
        session = _TestSession()
        try:
            job, contact = _seed(session)
            job_id, contact_id = job.id, contact.id
        finally:
            session.close()

        _install_fake_generator(monkeypatch)

        for _ in range(2):
            resp = client.post("/api/outreach/draft", json={
                "job_id": job_id, "contact_id": contact_id,
                "channel": "linkedin_note", "tone": "peer-pm",
            })
            assert resp.status_code == 200

        session = _TestSession()
        try:
            from backend.database.models import OutreachDraft
            count = session.query(OutreachDraft).filter(
                OutreachDraft.job_id == job_id,
                OutreachDraft.contact_id == contact_id,
                OutreachDraft.channel == "linkedin_note",
            ).count()
            assert count == 1
        finally:
            session.close()

    def test_404_for_missing_job(self, client, monkeypatch):
        _install_fake_generator(monkeypatch)
        resp = client.post("/api/outreach/draft", json={
            "job_id": 99999, "contact_id": 1,
            "channel": "linkedin_note", "tone": "peer-pm",
        })
        assert resp.status_code == 404

    def test_404_for_missing_contact(self, client, monkeypatch):
        session = _TestSession()
        try:
            job, _contact = _seed(session)
            job_id = job.id
        finally:
            session.close()
        _install_fake_generator(monkeypatch)

        resp = client.post("/api/outreach/draft", json={
            "job_id": job_id, "contact_id": 99999,
            "channel": "linkedin_note", "tone": "peer-pm",
        })
        assert resp.status_code == 404

    def test_503_when_generator_not_configured(self, client, monkeypatch):
        session = _TestSession()
        try:
            job, contact = _seed(session)
            job_id, contact_id = job.id, contact.id
        finally:
            session.close()

        _install_fake_generator(monkeypatch, configured=False)
        resp = client.post("/api/outreach/draft", json={
            "job_id": job_id, "contact_id": contact_id,
            "channel": "linkedin_note", "tone": "peer-pm",
        })
        assert resp.status_code == 503

    def test_400_for_invalid_channel(self, client, monkeypatch):
        session = _TestSession()
        try:
            job, contact = _seed(session)
            job_id, contact_id = job.id, contact.id
        finally:
            session.close()
        _install_fake_generator(monkeypatch)

        resp = client.post("/api/outreach/draft", json={
            "job_id": job_id, "contact_id": contact_id,
            "channel": "telegram", "tone": "peer-pm",
        })
        assert resp.status_code == 400

    def test_requires_api_key_when_set(self, client, monkeypatch):
        monkeypatch.setattr(api_main, "API_KEY", "secret")
        _install_fake_generator(monkeypatch)
        resp = client.post("/api/outreach/draft", json={
            "job_id": 1, "contact_id": 1,
            "channel": "linkedin_note", "tone": "peer-pm",
        })
        assert resp.status_code == 401


class TestListJobOutreach:
    def test_returns_drafts(self, client):
        session = _TestSession()
        try:
            job, contact = _seed(session)
            upsert_outreach_draft(
                session, job_id=job.id, contact_id=contact.id,
                channel="linkedin_note", tone="peer-pm", body="hi",
            )
            job_id = job.id
        finally:
            session.close()

        resp = client.get(f"/api/jobs/{job_id}/outreach")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert len(data["drafts"]) == 1
        assert data["drafts"][0]["channel"] == "linkedin_note"

    def test_empty_drafts_for_unseeded_job(self, client):
        session = _TestSession()
        try:
            job, _ = _seed(session)
            job_id = job.id
        finally:
            session.close()
        resp = client.get(f"/api/jobs/{job_id}/outreach")
        assert resp.status_code == 200
        assert resp.json()["drafts"] == []

    def test_404_when_job_missing(self, client):
        assert client.get("/api/jobs/99999/outreach").status_code == 404


class TestUpdateStatus:
    def test_patch_moves_status(self, client):
        session = _TestSession()
        try:
            job, contact = _seed(session)
            d = upsert_outreach_draft(
                session, job_id=job.id, contact_id=contact.id,
                channel="email", tone="peer-pm", body="v",
            )
            draft_id = d.id
        finally:
            session.close()

        resp = client.patch(f"/api/outreach/{draft_id}", json={"status": "sent"})
        assert resp.status_code == 200
        assert resp.json()["status"] == "sent"

    def test_400_on_invalid_status(self, client):
        session = _TestSession()
        try:
            job, contact = _seed(session)
            d = upsert_outreach_draft(
                session, job_id=job.id, contact_id=contact.id,
                channel="email", tone="peer-pm", body="v",
            )
            draft_id = d.id
        finally:
            session.close()
        resp = client.patch(f"/api/outreach/{draft_id}", json={"status": "yeeted"})
        assert resp.status_code == 400

    def test_404_for_missing_draft(self, client):
        resp = client.patch("/api/outreach/99999", json={"status": "sent"})
        assert resp.status_code == 404
