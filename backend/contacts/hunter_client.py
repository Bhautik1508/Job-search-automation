"""
Hunter.io fallback provider for email discovery — Phase 7.5.

Two endpoints we care about:
  - domain-search  →  company domain → list of emails + people
  - email-finder   →  company + name → a single email guess (not used yet)

Hunter surfaces role_type only weakly (department = "hr" etc.) — the
classifier in apollo_client.py handles the mapping from title → role_type
consistently for both providers.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from backend.config import HUNTER_API_BASE_URL, HUNTER_API_KEY
from backend.contacts.apollo_client import ApolloContact, _classify_role, _classify_confidence


class HunterClient:
    """
    Thin Hunter.io wrapper. Emits ApolloContact-shaped records so the
    enrichment pipeline treats Apollo + Hunter results uniformly.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 15.0,
        http_client: httpx.Client | None = None,
    ):
        self.api_key = api_key if api_key is not None else HUNTER_API_KEY
        self.base_url = (base_url or HUNTER_API_BASE_URL).rstrip("/")
        self.timeout = timeout
        self._injected_client = http_client

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search_people_at_company(
        self,
        company: str,
        *,
        domain: str | None = None,
        per_page: int = 10,
    ) -> list[ApolloContact]:
        """
        Domain-search. Hunter requires either a `domain` OR a `company` —
        passing both is fine; their matcher falls back to company-name
        lookup when domain is absent, which is our common case.

        Returns [] on any non-200, network error, or malformed payload.
        """
        if not self.is_configured:
            return []

        params: dict[str, Any] = {
            "api_key": self.api_key,
            "company": company,
            "limit": per_page,
        }
        if domain:
            params["domain"] = domain

        url = f"{self.base_url}/domain-search"
        try:
            client = self._injected_client or httpx.Client(timeout=self.timeout)
            try:
                resp = client.get(url, params=params)
            finally:
                if self._injected_client is None:
                    client.close()
        except httpx.HTTPError as e:
            print(f"[hunter] HTTP error: {type(e).__name__}: {e}")
            return []

        if resp.status_code != 200:
            body_preview = (resp.text or "")[:200]
            print(f"[hunter] non-200 ({resp.status_code}): {body_preview}")
            return []

        try:
            payload = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[hunter] JSON decode failed: {e}")
            return []

        data = payload.get("data") or {}
        emails = data.get("emails") or []
        if not isinstance(emails, list):
            return []

        contacts: list[ApolloContact] = []
        for entry in emails:
            contact = self._to_contact(entry, company=company)
            if contact is not None:
                contacts.append(contact)
        return contacts

    @staticmethod
    def _to_contact(entry: dict[str, Any], *, company: str) -> ApolloContact | None:
        first = (entry.get("first_name") or "").strip()
        last = (entry.get("last_name") or "").strip()
        name = f"{first} {last}".strip() or (entry.get("value") or "").split("@")[0]
        if not name:
            return None

        title = entry.get("position")
        linkedin_url = entry.get("linkedin")
        email = entry.get("value")
        # Hunter returns a per-email `confidence` 0-100. We re-bucket it
        # into our 0-1 range, but also factor in whether the title matched
        # the target role so low-title-confidence emails don't poison ranking.
        hunter_confidence_raw = entry.get("confidence")
        hunter_score = (float(hunter_confidence_raw) / 100.0) if hunter_confidence_raw else 0.3

        # Seed expected_role = "hm" because domain-search is title-agnostic;
        # _classify_role falls back to this only when the title is empty.
        role_type = _classify_role(title, fallback="hm")
        role_confidence = _classify_confidence(title, role_type)
        # Combine Hunter's email deliverability confidence with our role-classification
        # confidence — multiplying is the simplest way to say "both must be high".
        confidence = round(hunter_score * role_confidence, 2)

        return ApolloContact(
            name=name,
            title=title,
            company=company,
            linkedin_url=linkedin_url,
            email=email,
            role_type=role_type,
            confidence=confidence,
            raw=entry,
        )
