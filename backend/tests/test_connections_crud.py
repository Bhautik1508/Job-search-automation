"""
Phase R4 — Connection CRUD unit tests.

Covers normalize_company, upsert_connection (URL-first dedup, fallback by
name+normalized company), and find_connections_for_company (exact + fuzzy
fallback).
"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.database.crud import (
    count_connections,
    find_connections_for_company,
    get_connection_by_id,
    normalize_company,
    upsert_connection,
)
from backend.database.models import Base


@pytest.fixture
def session():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    s = SessionLocal()
    try:
        yield s
    finally:
        s.close()


class TestNormalizeCompany:
    def test_lowercases(self):
        assert normalize_company("Razorpay") == "razorpay"

    def test_strips_legal_suffix(self):
        assert normalize_company("Razorpay Pvt Ltd") == "razorpay"
        assert normalize_company("Acme, Inc.") == "acme"
        assert normalize_company("Stripe LLC") == "stripe"

    def test_strips_locale_tokens(self):
        assert normalize_company("Google India") == "google"
        assert normalize_company("Acme Technologies") == "acme"

    def test_keeps_distinguishing_tokens(self):
        # We intentionally don't collapse multi-token names — "Google Cloud"
        # is meaningfully different from plain "Google".
        assert normalize_company("Google Cloud") == "google cloud"

    def test_empty_input(self):
        assert normalize_company("") == ""
        assert normalize_company(None) == ""  # type: ignore[arg-type]

    def test_punctuation_only_yields_empty(self):
        assert normalize_company("...") == ""


class TestUpsertConnection:
    def test_creates_new_row(self, session):
        row, created = upsert_connection(
            session,
            name="Bob Peer",
            company="Razorpay",
            current_title="Senior PM",
            linkedin_url="https://linkedin.com/in/bobpeer",
        )
        assert created is True
        assert row.id is not None
        assert row.company_normalized == "razorpay"
        assert row.source == "csv"

    def test_dedup_by_linkedin_url_updates_in_place(self, session):
        first, created = upsert_connection(
            session,
            name="Bob Peer",
            company="Razorpay",
            linkedin_url="https://linkedin.com/in/bobpeer",
        )
        assert created is True
        # Same URL, different company name — should update, not insert.
        second, created2 = upsert_connection(
            session,
            name="Bob Peer",
            company="Razorpay India",
            current_title="VP Product",
            linkedin_url="https://linkedin.com/in/bobpeer",
        )
        assert created2 is False
        assert second.id == first.id
        assert second.company == "Razorpay India"
        assert second.current_title == "VP Product"
        assert count_connections(session) == 1

    def test_dedup_by_name_and_normalized_company_when_no_url(self, session):
        first, created = upsert_connection(
            session, name="Bob Peer", company="Razorpay",
        )
        assert created is True
        second, created2 = upsert_connection(
            session, name="Bob Peer", company="Razorpay Pvt Ltd",
        )
        # "Razorpay" and "Razorpay Pvt Ltd" normalize to the same key.
        assert created2 is False
        assert second.id == first.id
        assert count_connections(session) == 1

    def test_two_distinct_connections_at_same_company(self, session):
        upsert_connection(session, name="Bob Peer", company="Razorpay")
        upsert_connection(session, name="Carol Other", company="Razorpay")
        assert count_connections(session) == 2

    def test_get_connection_by_id(self, session):
        row, _ = upsert_connection(session, name="Bob", company="Razorpay")
        assert get_connection_by_id(session, row.id).name == "Bob"
        assert get_connection_by_id(session, 99999) is None


class TestFindConnectionsForCompany:
    def test_exact_normalized_match(self, session):
        upsert_connection(session, name="Bob Peer", company="Razorpay")
        # Job company has a different legal suffix — should still match.
        results = find_connections_for_company(session, "Razorpay Pvt Ltd")
        assert len(results) == 1
        assert results[0].name == "Bob Peer"

    def test_returns_empty_for_unknown_company(self, session):
        upsert_connection(session, name="Bob Peer", company="Razorpay")
        assert find_connections_for_company(session, "Stripe") == []

    def test_returns_empty_for_blank_company(self, session):
        upsert_connection(session, name="Bob Peer", company="Razorpay")
        assert find_connections_for_company(session, "") == []

    def test_fuzzy_fallback_on_head_token(self, session):
        # Connection at "Google" should surface for "Google Cloud" job
        # via the head-token fallback.
        upsert_connection(session, name="Bob Peer", company="Google")
        results = find_connections_for_company(session, "Google Cloud")
        assert len(results) == 1

    def test_limit_caps_results(self, session):
        for i in range(5):
            upsert_connection(
                session, name=f"Person {i}", company="Razorpay",
                linkedin_url=f"u{i}",
            )
        results = find_connections_for_company(session, "Razorpay", limit=3)
        assert len(results) == 3
