"""
Phase R4 — API tests for the warm-referral endpoints.

Covers:
  POST /api/connections/import
  GET  /api/jobs/{id}/connections
  POST /api/outreach/referral-ask
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
from backend.database.crud import upsert_connection, upsert_contact
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
        session.execute(text("DELETE FROM connections"))
        session.execute(text("DELETE FROM jobs"))
        session.execute(text("DELETE FROM scrape_scans"))
        session.commit()
    finally:
        session.close()


@pytest.fixture
def client():
    return TestClient(app)


def _seed_job(session, *, company="Razorpay") -> Job:
    job = Job(
        title="Product Manager",
        company=company,
        location="Bangalore",
        source_portal="naukri",
        source_engine="test",
        dedup_hash=f"h_{datetime.now(timezone.utc).timestamp()}",
        date_scraped=datetime.now(timezone.utc),
    )
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def _install_fake_generator(monkeypatch):
    """Stub the generator so the route exercises persistence, not Gemini."""
    import backend.outreach.generator as gen_module

    canned = GenerationResult(
        subject=None,
        body="Hey Bob — could you intro me to Alice for the PM role at Razorpay?",
        portfolio_ids_used=[],
        model="gemini-test",
        channel="referral_ask",
        tone="peer-pm",
    )

    class _Stub:
        def __init__(self, *a, **kw):
            self.is_configured = True

        def generate(self, *, job, contact, channel, tone, connection=None, **kw):
            from backend.outreach.generator import CHANNELS, TONES
            if channel not in CHANNELS:
                raise ValueError(f"Unknown channel {channel!r}")
            if tone not in TONES:
                raise ValueError(f"Unknown tone {tone!r}")
            if channel == "referral_ask" and connection is None:
                raise ValueError("referral_ask requires a connection (the warm peer to DM).")
            return canned

    monkeypatch.setattr(gen_module, "OutreachGenerator", _Stub)


# ------------------------------------------------------------------
# POST /api/connections/import
# ------------------------------------------------------------------

class TestImportConnections:
    def test_imports_csv(self, client):
        csv_text = (
            "name,company,current_title,linkedin_url\n"
            "Bob Peer,Razorpay,Senior PM,https://linkedin.com/in/bobpeer\n"
            "Alice Smith,Stripe,Product Lead,https://linkedin.com/in/alicesmith\n"
        )
        resp = client.post(
            "/api/connections/import",
            json={"csv": csv_text, "source": "linkedin"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["imported"] == 2
        assert data["updated"] == 0
        assert data["total_connections"] == 2

    def test_reimport_updates_existing(self, client):
        csv_text = (
            "name,company,linkedin_url\n"
            "Bob Peer,Razorpay,https://linkedin.com/in/bobpeer\n"
        )
        first = client.post("/api/connections/import", json={"csv": csv_text}).json()
        assert first["imported"] == 1
        second = client.post("/api/connections/import", json={"csv": csv_text}).json()
        assert second["imported"] == 0
        assert second["updated"] == 1
        assert second["total_connections"] == 1

    def test_400_when_csv_blank(self, client):
        resp = client.post("/api/connections/import", json={"csv": ""})
        assert resp.status_code == 400

    def test_warnings_surface_in_response(self, client):
        csv_text = "name,company\nBob Peer,Razorpay\nNoCompany,\n"
        resp = client.post("/api/connections/import", json={"csv": csv_text}).json()
        assert resp["imported"] == 1
        assert resp["skipped"] == 1
        assert resp["warnings"]

    def test_requires_api_key_when_set(self, client, monkeypatch):
        monkeypatch.setattr(api_main, "API_KEY", "secret")
        resp = client.post("/api/connections/import", json={"csv": "name,company\n"})
        assert resp.status_code == 401


# ------------------------------------------------------------------
# GET /api/jobs/{id}/connections
# ------------------------------------------------------------------

class TestListJobConnections:
    def test_returns_connections_at_company(self, client):
        session = _TestSession()
        try:
            job = _seed_job(session)
            upsert_connection(session, name="Bob Peer", company="Razorpay")
            job_id = job.id
        finally:
            session.close()

        resp = client.get(f"/api/jobs/{job_id}/connections")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["company"] == "Razorpay"
        assert len(data["connections"]) == 1
        assert data["connections"][0]["name"] == "Bob Peer"

    def test_fuzzy_matches_legal_suffix(self, client):
        session = _TestSession()
        try:
            job = _seed_job(session, company="Razorpay Pvt Ltd")
            upsert_connection(session, name="Bob Peer", company="Razorpay")
            job_id = job.id
        finally:
            session.close()

        resp = client.get(f"/api/jobs/{job_id}/connections")
        assert resp.status_code == 200
        assert len(resp.json()["connections"]) == 1

    def test_empty_when_no_match(self, client):
        session = _TestSession()
        try:
            job = _seed_job(session, company="Stripe")
            upsert_connection(session, name="Bob Peer", company="Razorpay")
            job_id = job.id
        finally:
            session.close()

        resp = client.get(f"/api/jobs/{job_id}/connections")
        assert resp.status_code == 200
        assert resp.json()["connections"] == []

    def test_404_when_job_missing(self, client):
        assert client.get("/api/jobs/99999/connections").status_code == 404


# ------------------------------------------------------------------
# POST /api/outreach/referral-ask
# ------------------------------------------------------------------

class TestReferralAsk:
    def test_creates_referral_ask_draft(self, client, monkeypatch):
        session = _TestSession()
        try:
            job = _seed_job(session)
            connection, _ = upsert_connection(
                session, name="Bob Peer", company="Razorpay",
                linkedin_url="https://linkedin.com/in/bobpeer",
            )
            target = upsert_contact(
                session, name="Alice Smith", company="Razorpay",
                role_type="hm", source_provider="apollo", linkedin_url="alice",
            )
            job_id, conn_id, target_id = job.id, connection.id, target.id
        finally:
            session.close()

        _install_fake_generator(monkeypatch)

        resp = client.post(
            "/api/outreach/referral-ask",
            json={
                "job_id": job_id,
                "connection_id": conn_id,
                "target_contact_id": target_id,
                "tone": "peer-pm",
            },
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["channel"] == "referral_ask"
        assert data["connection_id"] == conn_id
        # contact_id stores the HM (intro target), not the warm peer.
        assert data["contact_id"] == target_id
        assert "Bob" in data["body"]

    def test_404_when_job_missing(self, client, monkeypatch):
        _install_fake_generator(monkeypatch)
        resp = client.post(
            "/api/outreach/referral-ask",
            json={
                "job_id": 99999, "connection_id": 1,
                "target_contact_id": 1, "tone": "peer-pm",
            },
        )
        assert resp.status_code == 404

    def test_404_when_connection_missing(self, client, monkeypatch):
        session = _TestSession()
        try:
            job = _seed_job(session)
            target = upsert_contact(
                session, name="Alice Smith", company="Razorpay",
                role_type="hm", source_provider="apollo", linkedin_url="a",
            )
            job_id, target_id = job.id, target.id
        finally:
            session.close()

        _install_fake_generator(monkeypatch)
        resp = client.post(
            "/api/outreach/referral-ask",
            json={
                "job_id": job_id, "connection_id": 99999,
                "target_contact_id": target_id, "tone": "peer-pm",
            },
        )
        assert resp.status_code == 404

    def test_404_when_target_contact_missing(self, client, monkeypatch):
        session = _TestSession()
        try:
            job = _seed_job(session)
            connection, _ = upsert_connection(
                session, name="Bob Peer", company="Razorpay",
            )
            job_id, conn_id = job.id, connection.id
        finally:
            session.close()

        _install_fake_generator(monkeypatch)
        resp = client.post(
            "/api/outreach/referral-ask",
            json={
                "job_id": job_id, "connection_id": conn_id,
                "target_contact_id": 99999, "tone": "peer-pm",
            },
        )
        assert resp.status_code == 404

    def test_requires_api_key_when_set(self, client, monkeypatch):
        monkeypatch.setattr(api_main, "API_KEY", "secret")
        _install_fake_generator(monkeypatch)
        resp = client.post(
            "/api/outreach/referral-ask",
            json={
                "job_id": 1, "connection_id": 1,
                "target_contact_id": 1, "tone": "peer-pm",
            },
        )
        assert resp.status_code == 401
