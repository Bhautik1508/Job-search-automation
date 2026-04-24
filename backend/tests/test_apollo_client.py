"""
Unit tests for the Apollo.io client.

We don't hit the network — a fake httpx.Client is injected that records
requests and returns canned payloads.
"""

from __future__ import annotations

import pytest

from backend.contacts.apollo_client import (
    ApolloClient,
    ApolloContact,
    iter_unique_contacts,
    _classify_role,
    _classify_confidence,
)


class _FakeResponse:
    def __init__(self, status_code: int, json_body=None, text: str = ""):
        self.status_code = status_code
        self._json = json_body
        self.text = text

    def json(self):
        if self._json is None:
            raise ValueError("no json")
        return self._json


class _FakeClient:
    """httpx.Client-shaped stub that replays queued responses."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def post(self, url, json=None, headers=None):
        self.calls.append({"url": url, "json": json, "headers": headers})
        if not self._responses:
            return _FakeResponse(200, {"people": []})
        return self._responses.pop(0)

    def close(self):  # pragma: no cover — injected client lifecycle owned by caller
        pass


class TestIsConfigured:
    def test_empty_key_not_configured(self):
        c = ApolloClient(api_key="")
        assert c.is_configured is False

    def test_key_present(self):
        c = ApolloClient(api_key="abc")
        assert c.is_configured is True

    def test_search_skipped_when_unconfigured(self):
        c = ApolloClient(api_key="")
        assert c.search_people_at_company("Razorpay") == []


class TestSearchPeopleAtCompany:
    def test_maps_hm_response(self):
        hm_payload = {
            "people": [
                {
                    "name": "Alice Smith",
                    "title": "Head of Product",
                    "linkedin_url": "https://linkedin.com/in/alice",
                    "email": "alice@razorpay.com",
                }
            ]
        }
        recruiter_payload = {"people": []}
        fake = _FakeClient([
            _FakeResponse(200, hm_payload),
            _FakeResponse(200, recruiter_payload),
        ])
        client = ApolloClient(api_key="test-key", http_client=fake)

        contacts = client.search_people_at_company("Razorpay")
        assert len(contacts) == 1
        assert contacts[0].name == "Alice Smith"
        assert contacts[0].role_type == "hm"
        assert contacts[0].email == "alice@razorpay.com"
        assert contacts[0].confidence == 0.9

    def test_hides_locked_emails(self):
        payload = {
            "people": [
                {
                    "name": "Bob",
                    "title": "Product Lead",
                    "email": "email_not_unlocked@domain.apollo.io",
                }
            ]
        }
        fake = _FakeClient([
            _FakeResponse(200, payload),
            _FakeResponse(200, {"people": []}),
        ])
        client = ApolloClient(api_key="test-key", http_client=fake)
        [contact] = client.search_people_at_company("Razorpay")
        assert contact.email is None

    def test_runs_both_role_queries(self):
        """One search per role — HM + recruiter."""
        fake = _FakeClient([
            _FakeResponse(200, {"people": []}),
            _FakeResponse(200, {"people": []}),
        ])
        ApolloClient(api_key="k", http_client=fake).search_people_at_company("X")
        assert len(fake.calls) == 2

    def test_non_200_returns_empty(self):
        fake = _FakeClient([
            _FakeResponse(401, text="Unauthorized"),
            _FakeResponse(200, {"people": []}),
        ])
        client = ApolloClient(api_key="k", http_client=fake)
        # First call fails, second returns empty → overall empty
        assert client.search_people_at_company("X") == []

    def test_malformed_json_returns_empty(self):
        fake = _FakeClient([
            _FakeResponse(200, json_body=None, text="<html>error</html>"),
            _FakeResponse(200, {"people": []}),
        ])
        client = ApolloClient(api_key="k", http_client=fake)
        assert client.search_people_at_company("X") == []

    def test_name_reconstruction_from_first_last(self):
        payload = {
            "people": [
                {"first_name": "Carol", "last_name": "Kim", "title": "Recruiter"}
            ]
        }
        fake = _FakeClient([
            _FakeResponse(200, {"people": []}),
            _FakeResponse(200, payload),
        ])
        client = ApolloClient(api_key="k", http_client=fake)
        contacts = client.search_people_at_company("X")
        assert len(contacts) == 1
        assert contacts[0].name == "Carol Kim"
        assert contacts[0].role_type == "recruiter"

    def test_drops_anonymous_people(self):
        payload = {"people": [{"title": "Recruiter"}]}  # no name, no first/last
        fake = _FakeClient([
            _FakeResponse(200, {"people": []}),
            _FakeResponse(200, payload),
        ])
        client = ApolloClient(api_key="k", http_client=fake)
        assert client.search_people_at_company("X") == []

    def test_api_key_sent_in_header_and_body(self):
        fake = _FakeClient([
            _FakeResponse(200, {"people": []}),
            _FakeResponse(200, {"people": []}),
        ])
        ApolloClient(api_key="secret-k", http_client=fake).search_people_at_company("X")
        first = fake.calls[0]
        assert first["headers"]["X-Api-Key"] == "secret-k"
        assert first["json"]["api_key"] == "secret-k"
        assert first["json"]["organization_names"] == ["X"]


class TestClassifyRole:
    @pytest.mark.parametrize(
        "title, expected",
        [
            ("Head of Product", "hm"),
            ("VP Product", "hm"),
            ("Senior Product Manager", "hm"),
            ("Technical Recruiter", "recruiter"),
            ("Talent Acquisition Partner", "recruiter"),
            ("Something Weird", "hm"),   # falls back to expected_role
        ],
    )
    def test_classification(self, title, expected):
        assert _classify_role(title, fallback="hm") == expected

    def test_empty_title_uses_fallback(self):
        assert _classify_role(None, fallback="recruiter") == "recruiter"


class TestClassifyConfidence:
    def test_clean_match_high_confidence(self):
        assert _classify_confidence("Head of Product", "hm") == 0.9

    def test_mismatched_bucket(self):
        # Queried as HM but title is a recruiter title
        assert _classify_confidence("Technical Recruiter", "hm") == 0.6

    def test_no_title(self):
        assert _classify_confidence(None, "hm") == 0.4


class TestIterUniqueContacts:
    def _make(self, name: str, linkedin_url: str | None = None, company: str = "X"):
        return ApolloContact(
            name=name, title="PM", company=company,
            linkedin_url=linkedin_url, email=None,
            role_type="hm", confidence=0.9,
        )

    def test_dedupes_by_linkedin_url(self):
        a = self._make("A", linkedin_url="u1")
        a_dup = self._make("A different", linkedin_url="u1")
        b = self._make("B", linkedin_url="u2")
        out = iter_unique_contacts([a, a_dup, b])
        assert [c.name for c in out] == ["A", "B"]

    def test_dedupes_by_name_company_when_no_url(self):
        a = self._make("Alice", company="X")
        a_dup = self._make("Alice", company="X")
        a_other_co = self._make("Alice", company="Y")
        out = iter_unique_contacts([a, a_dup, a_other_co])
        assert len(out) == 2

    def test_different_urls_not_deduped(self):
        out = iter_unique_contacts([
            self._make("Alice", linkedin_url="u1"),
            self._make("Alice", linkedin_url="u2"),
        ])
        assert len(out) == 2
