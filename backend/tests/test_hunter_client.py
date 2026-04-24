"""
Unit tests for the Hunter.io fallback client.

Network is stubbed — an injected httpx-shaped client replays canned responses.
"""

from __future__ import annotations

import pytest

from backend.contacts.hunter_client import HunterClient


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
    """httpx.Client-shaped stub used only for GET /domain-search."""

    def __init__(self, responses: list[_FakeResponse]):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def get(self, url, params=None):
        self.calls.append({"url": url, "params": params})
        if not self._responses:
            return _FakeResponse(200, {"data": {"emails": []}})
        return self._responses.pop(0)

    def close(self):  # pragma: no cover
        pass


class TestIsConfigured:
    def test_empty_key_not_configured(self):
        assert HunterClient(api_key="").is_configured is False

    def test_key_present(self):
        assert HunterClient(api_key="k").is_configured is True

    def test_search_skipped_when_unconfigured(self):
        assert HunterClient(api_key="").search_people_at_company("X") == []


class TestSearchPeopleAtCompany:
    def test_maps_email_entry_to_contact(self):
        payload = {
            "data": {
                "emails": [
                    {
                        "value": "alice@razorpay.com",
                        "first_name": "Alice",
                        "last_name": "Smith",
                        "position": "Head of Product",
                        "linkedin": "https://linkedin.com/in/alice",
                        "confidence": 90,
                    }
                ]
            }
        }
        fake = _FakeClient([_FakeResponse(200, payload)])
        client = HunterClient(api_key="k", http_client=fake)

        [c] = client.search_people_at_company("Razorpay")
        assert c.name == "Alice Smith"
        assert c.email == "alice@razorpay.com"
        assert c.role_type == "hm"
        # hunter_score 0.9 * role_confidence 0.9 = 0.81
        assert c.confidence == pytest.approx(0.81, abs=1e-2)

    def test_falls_back_to_email_local_part_for_name(self):
        payload = {
            "data": {
                "emails": [
                    {
                        "value": "bob@acme.com",
                        "position": "Technical Recruiter",
                        "confidence": 80,
                    }
                ]
            }
        }
        fake = _FakeClient([_FakeResponse(200, payload)])
        [c] = HunterClient(api_key="k", http_client=fake).search_people_at_company("Acme")
        assert c.name == "bob"
        assert c.role_type == "recruiter"

    def test_non_200_returns_empty(self):
        fake = _FakeClient([_FakeResponse(401, text="Unauthorized")])
        assert HunterClient(api_key="k", http_client=fake).search_people_at_company("X") == []

    def test_malformed_json_returns_empty(self):
        fake = _FakeClient([_FakeResponse(200, json_body=None, text="<html/>")])
        assert HunterClient(api_key="k", http_client=fake).search_people_at_company("X") == []

    def test_missing_emails_array_returns_empty(self):
        fake = _FakeClient([_FakeResponse(200, {"data": {}})])
        assert HunterClient(api_key="k", http_client=fake).search_people_at_company("X") == []

    def test_api_key_in_params(self):
        fake = _FakeClient([_FakeResponse(200, {"data": {"emails": []}})])
        HunterClient(api_key="secret", http_client=fake).search_people_at_company("X")
        assert fake.calls[0]["params"]["api_key"] == "secret"
        assert fake.calls[0]["params"]["company"] == "X"

    def test_domain_param_forwarded(self):
        fake = _FakeClient([_FakeResponse(200, {"data": {"emails": []}})])
        HunterClient(api_key="k", http_client=fake).search_people_at_company(
            "Razorpay", domain="razorpay.com"
        )
        assert fake.calls[0]["params"]["domain"] == "razorpay.com"

    def test_missing_confidence_uses_low_default(self):
        payload = {
            "data": {
                "emails": [
                    {
                        "value": "c@x.com",
                        "first_name": "C",
                        "last_name": "K",
                        "position": "Head of Product",
                        # no confidence field
                    }
                ]
            }
        }
        fake = _FakeClient([_FakeResponse(200, payload)])
        [c] = HunterClient(api_key="k", http_client=fake).search_people_at_company("X")
        # 0.3 (fallback hunter_score) * 0.9 (role match) = 0.27
        assert c.confidence == pytest.approx(0.27, abs=1e-2)
