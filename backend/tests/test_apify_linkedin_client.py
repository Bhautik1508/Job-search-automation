"""
Unit tests for the Apify LinkedIn profile-scraper fallback client.

The Apify Python client isn't touched — we inject a stub that mimics
`client.actor(id).call(run_input=...)` + `.dataset(id).list_items().items`.
"""

from __future__ import annotations

import pytest

from backend.contacts.apify_linkedin_client import ApifyLinkedInClient


class _FakeListing:
    def __init__(self, items):
        self.items = items


class _FakeDataset:
    def __init__(self, items):
        self._items = items

    def list_items(self):
        return _FakeListing(self._items)


class _FakeActorRunner:
    def __init__(self, items_per_call):
        self._queue = list(items_per_call)
        self.calls: list[dict] = []

    def call(self, run_input=None):
        self.calls.append({"run_input": run_input})
        if not self._queue:
            return {"defaultDatasetId": "ds_empty"}
        return {"defaultDatasetId": "ds_0"}

    def pop_next_items(self):
        return self._queue.pop(0) if self._queue else []


class _FakeApifyClient:
    def __init__(self, items_per_call):
        self._runner = _FakeActorRunner(items_per_call)
        self._datasets: dict[str, list] = {}
        self._next_dataset_idx = 0
        self._items_per_call = list(items_per_call)

    def actor(self, actor_id):
        class _ActorHandle:
            def __init__(self, client):
                self._client = client

            def call(self, run_input=None):
                self._client._runner.calls.append({"run_input": run_input})
                items = (
                    self._client._items_per_call.pop(0)
                    if self._client._items_per_call
                    else []
                )
                ds_id = f"ds_{self._client._next_dataset_idx}"
                self._client._next_dataset_idx += 1
                self._client._datasets[ds_id] = items
                return {"defaultDatasetId": ds_id}

        return _ActorHandle(self)

    def dataset(self, ds_id):
        return _FakeDataset(self._datasets.get(ds_id, []))

    @property
    def calls(self):
        return self._runner.calls


class TestIsConfigured:
    def test_unconfigured_without_token(self):
        c = ApifyLinkedInClient(api_token="", actor_id="x")
        assert c.is_configured is False

    def test_configured_when_both_present(self):
        c = ApifyLinkedInClient(api_token="t", actor_id="x")
        assert c.is_configured is True

    def test_returns_empty_when_unconfigured(self):
        c = ApifyLinkedInClient(api_token="", actor_id="x")
        assert c.search_people_at_company("Razorpay") == []


class TestItemMapping:
    def test_full_name_field(self):
        item = {
            "fullName": "Alice Smith",
            "headline": "Head of Product at Razorpay",
            "profileUrl": "https://linkedin.com/in/alice",
        }
        fake = _FakeApifyClient([[item], []])  # 1 HM item, 0 recruiter items
        c = ApifyLinkedInClient(api_token="t", actor_id="x", api_client=fake)
        contacts = c.search_people_at_company("Razorpay")
        assert len(contacts) == 1
        assert contacts[0].name == "Alice Smith"
        assert contacts[0].role_type == "hm"
        # Confidence is down-weighted by 0.8 from the base role-match score.
        assert contacts[0].confidence == pytest.approx(0.72, abs=1e-2)

    def test_first_last_name_composition(self):
        item = {
            "firstName": "Bob",
            "lastName": "Kim",
            "title": "Technical Recruiter",
            "linkedinUrl": "https://linkedin.com/in/bob",
        }
        fake = _FakeApifyClient([[], [item]])  # 0 HM, 1 recruiter
        c = ApifyLinkedInClient(api_token="t", actor_id="x", api_client=fake)
        [contact] = c.search_people_at_company("Razorpay")
        assert contact.name == "Bob Kim"
        assert contact.role_type == "recruiter"

    def test_anonymous_items_dropped(self):
        fake = _FakeApifyClient([[{"headline": "PM"}], []])
        assert ApifyLinkedInClient(api_token="t", actor_id="x", api_client=fake).search_people_at_company("X") == []

    def test_non_dict_items_dropped(self):
        fake = _FakeApifyClient([["just-a-string"], []])
        assert ApifyLinkedInClient(api_token="t", actor_id="x", api_client=fake).search_people_at_company("X") == []


class TestRunOrchestration:
    def test_runs_two_actor_calls_for_two_roles(self):
        fake = _FakeApifyClient([[], []])
        ApifyLinkedInClient(api_token="t", actor_id="x", api_client=fake).search_people_at_company("Razorpay")
        assert len(fake.calls) == 2

    def test_run_input_contains_company_and_keyword(self):
        fake = _FakeApifyClient([[], []])
        ApifyLinkedInClient(api_token="t", actor_id="x", api_client=fake).search_people_at_company("Razorpay")
        first = fake.calls[0]["run_input"]
        assert "Razorpay" in first["searchQueries"][0]
        assert first["company"] == "Razorpay"

    def test_actor_call_exception_returns_empty(self):
        class _Exploding:
            def actor(self, _id):
                class _A:
                    def call(self, run_input=None):
                        raise RuntimeError("rate limited")
                return _A()

            def dataset(self, _id):  # pragma: no cover
                return _FakeDataset([])

        c = ApifyLinkedInClient(api_token="t", actor_id="x", api_client=_Exploding())
        assert c.search_people_at_company("X") == []
