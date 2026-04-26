"""
API tests for Phase 7 contact endpoints.

We patch EnrichmentPipeline at import time so the /api/enrich-contacts
route exercises the route + schema marshaling without hitting Apollo.
"""

from __future__ import annotations

import pytest
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.models import Base, Contact, Job, JobContact
from backend.database.crud import link_job_to_contact, upsert_contact
from backend.api.main import app
import backend.api.main as api_main


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


def _seed_job(session, **kwargs) -> Job:
    defaults = dict(
        title="Product Manager",
        company="Razorpay",
        location="Bangalore",
        source_portal="naukri",
        source_engine="test",
        dedup_hash=f"hash_{kwargs.get('company', 'Razorpay')}_{kwargs.get('title', 'pm')}",
        date_scraped=datetime.now(timezone.utc),
    )
    defaults.update(kwargs)
    job = Job(**defaults)
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


class TestListJobContacts:
    def test_empty_for_unlinked_job(self, client):
        session = _TestSession()
        try:
            job = _seed_job(session)
            job_id = job.id
        finally:
            session.close()

        resp = client.get(f"/api/jobs/{job_id}/contacts")
        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == job_id
        assert data["company"] == "Razorpay"
        assert data["contacts"] == []

    def test_returns_linked_contacts(self, client):
        session = _TestSession()
        try:
            job = _seed_job(session)
            c = upsert_contact(
                session, name="Alice", company="Razorpay",
                role_type="hm", source_provider="apollo",
                linkedin_url="u1", confidence=0.9,
                title="Head of Product",
            )
            link_job_to_contact(
                session, job_id=job.id, contact_id=c.id,
                provider="apollo", confidence=0.8,
            )
            job_id = job.id
        finally:
            session.close()

        resp = client.get(f"/api/jobs/{job_id}/contacts")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["contacts"]) == 1
        contact = data["contacts"][0]
        assert contact["name"] == "Alice"
        assert contact["role_type"] == "hm"
        assert contact["link_provider"] == "apollo"
        assert contact["link_confidence"] == 0.8

    def test_404_when_job_missing(self, client):
        resp = client.get("/api/jobs/99999/contacts")
        assert resp.status_code == 404


class TestEnrichContactsEndpoint:
    def test_requires_api_key_in_prod(self, client, monkeypatch):
        """When API_KEY is set and IS_PRODUCTION=True, missing header → 401."""
        monkeypatch.setattr(api_main, "API_KEY", "prod-secret")
        # No X-API-Key header
        resp = client.post("/api/enrich-contacts")
        assert resp.status_code == 401

    def test_404_for_unknown_job_id(self, client):
        resp = client.post("/api/enrich-contacts?job_id=99999")
        assert resp.status_code == 404

    def test_success_uses_pipeline(self, client, monkeypatch):
        """Swap EnrichmentPipeline for a stub that returns a known result."""
        from backend.contacts.enrichment_pipeline import EnrichmentResult
        import backend.contacts.enrichment_pipeline as pipeline_module

        class _StubPipeline:
            def __init__(self, session):
                self.session = session

            def run(self, jobs):
                return EnrichmentResult(
                    jobs_considered=1,
                    jobs_eligible=1,
                    jobs_enriched=1,
                    contacts_created=2,
                    links_created=2,
                )

            def enrich_job(self, job):
                return self.run([job])

        monkeypatch.setattr(pipeline_module, "EnrichmentPipeline", _StubPipeline)

        # Seed one job so the endpoint has something to pass
        session = _TestSession()
        try:
            _seed_job(
                session,
                verdict="STRONG_FIT",
                relevancy_score=90.0,
                status="new",
            )
        finally:
            session.close()

        resp = client.post("/api/enrich-contacts?limit=5")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "completed"
        assert data["jobs_enriched"] == 1
        assert data["contacts_created"] == 2
