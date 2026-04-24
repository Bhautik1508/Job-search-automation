"""
Apollo.io people-search client — Phase 7 primary contact provider.

Docs: https://apolloio.github.io/apollo-api-docs/?shell#people-search

We intentionally keep the wrapper thin: a single `search_people_at_company`
method that the enrichment pipeline calls. Classification of role_type
(hm vs recruiter) happens here based on the returned title string.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Iterable

import httpx

from backend.config import (
    APOLLO_API_BASE_URL,
    APOLLO_API_KEY,
    CONTACT_HM_TITLE_KEYWORDS,
    CONTACT_RECRUITER_TITLE_KEYWORDS,
)


@dataclass
class ApolloContact:
    """
    Normalized contact surfaced by Apollo.

    `raw` retains the original payload so enrichment can persist a debug
    blob per contact — useful when Apollo changes their response shape.
    """

    name: str
    title: str | None
    company: str
    linkedin_url: str | None
    email: str | None
    role_type: str           # hm | recruiter | referral
    confidence: float        # 0..1
    raw: dict[str, Any] = field(default_factory=dict)


class ApolloClient:
    """
    Minimal Apollo.io REST wrapper.

    - `is_configured` gates all callers — if APOLLO_API_KEY is missing,
      the enrichment pipeline logs and skips, rather than erroring.
    - `search_people_at_company` combines HM + recruiter searches into
      one public call, since both are typically needed per job.
    """

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout: float = 15.0,
        http_client: httpx.Client | None = None,
    ):
        self.api_key = api_key if api_key is not None else APOLLO_API_KEY
        self.base_url = (base_url or APOLLO_API_BASE_URL).rstrip("/")
        self.timeout = timeout
        # Dependency injection — tests pass a stub client. Production callers
        # let us lazily create a real httpx.Client on first use.
        self._injected_client = http_client

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    def search_people_at_company(
        self,
        company: str,
        *,
        per_page: int = 5,
    ) -> list[ApolloContact]:
        """
        Return up to `per_page` HM + `per_page` recruiter contacts at
        the given company. Two separate searches because Apollo's
        `person_titles` filter is OR-combined and we'd otherwise lose
        the role_type distinction.

        Returns empty list on any non-200 / network error — callers
        treat "no contacts" as a normal outcome, not a failure.
        """
        if not self.is_configured:
            return []

        contacts: list[ApolloContact] = []
        for role_type, keywords in (
            ("hm", CONTACT_HM_TITLE_KEYWORDS),
            ("recruiter", CONTACT_RECRUITER_TITLE_KEYWORDS),
        ):
            raw_people = self._search(
                company=company,
                person_titles=list(keywords),
                per_page=per_page,
            )
            for person in raw_people:
                c = self._to_contact(person, company=company, expected_role=role_type)
                if c is not None:
                    contacts.append(c)
        return contacts

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _search(
        self,
        *,
        company: str,
        person_titles: list[str],
        per_page: int,
    ) -> list[dict[str, Any]]:
        """Make a single POST /people/search call; return the `people` array."""
        payload = {
            "api_key": self.api_key,
            "q_organization_domains": "",
            "organization_names": [company],
            "person_titles": person_titles,
            "page": 1,
            "per_page": per_page,
        }
        url = f"{self.base_url}/people/search"
        headers = {
            "Content-Type": "application/json",
            "Cache-Control": "no-cache",
            "X-Api-Key": self.api_key,
        }

        try:
            client = self._injected_client or httpx.Client(timeout=self.timeout)
            try:
                resp = client.post(url, json=payload, headers=headers)
            finally:
                # Only close when we own the client. An injected client's
                # lifecycle belongs to whoever passed it in.
                if self._injected_client is None:
                    client.close()
        except httpx.HTTPError as e:
            print(f"[apollo] HTTP error: {type(e).__name__}: {e}")
            return []

        if resp.status_code != 200:
            body_preview = (resp.text or "")[:200]
            print(f"[apollo] non-200 ({resp.status_code}): {body_preview}")
            return []

        try:
            data = resp.json()
        except (json.JSONDecodeError, ValueError) as e:
            print(f"[apollo] JSON decode failed: {e}")
            return []

        people = data.get("people") or []
        return people if isinstance(people, list) else []

    @staticmethod
    def _to_contact(
        person: dict[str, Any],
        *,
        company: str,
        expected_role: str,
    ) -> ApolloContact | None:
        """Map an Apollo person payload to our ApolloContact shape."""
        name = (person.get("name") or "").strip()
        if not name:
            first = (person.get("first_name") or "").strip()
            last = (person.get("last_name") or "").strip()
            name = f"{first} {last}".strip()
        if not name:
            return None

        title = person.get("title")
        linkedin_url = person.get("linkedin_url")
        email = person.get("email")
        # Apollo returns "email_not_unlocked@domain.apollo.io" placeholders
        # when the user hasn't paid for that contact's email — treat as None.
        if email and "email_not_unlocked" in email.lower():
            email = None

        confidence = _classify_confidence(title, expected_role)
        role_type = _classify_role(title, fallback=expected_role)

        return ApolloContact(
            name=name,
            title=title,
            company=company,
            linkedin_url=linkedin_url,
            email=email,
            role_type=role_type,
            confidence=confidence,
            raw=person,
        )


def _classify_role(title: str | None, *, fallback: str) -> str:
    """
    Classify a free-form title as 'hm' or 'recruiter'.

    HM check runs first because some titles like "Technical Recruiting Lead"
    contain the word "product" tangentially — we'd rather bias toward the
    HM label when both patterns match. Final fallback is the role_type
    Apollo was queried with.
    """
    if not title:
        return fallback
    t = title.lower()
    for k in CONTACT_HM_TITLE_KEYWORDS:
        if k in t:
            return "hm"
    for k in CONTACT_RECRUITER_TITLE_KEYWORDS:
        if k in t:
            return "recruiter"
    return fallback


def _classify_confidence(title: str | None, expected_role: str) -> float:
    """
    Heuristic confidence: 0.9 when title cleanly matches the queried role,
    0.6 when it matched the wrong bucket (still a valid contact, just
    mis-bucketed), 0.4 when there's no title at all.
    """
    if not title:
        return 0.4
    actual = _classify_role(title, fallback=expected_role)
    return 0.9 if actual == expected_role else 0.6


def iter_unique_contacts(contacts: Iterable[ApolloContact]) -> list[ApolloContact]:
    """
    De-duplicate a batch on linkedin_url (primary) or (name, company).
    Apollo occasionally returns the same person under multiple title searches.
    """
    seen_urls: set[str] = set()
    seen_names: set[tuple[str, str]] = set()
    out: list[ApolloContact] = []
    for c in contacts:
        key_url = (c.linkedin_url or "").strip().lower()
        key_name = (c.name.strip().lower(), c.company.strip().lower())
        if key_url and key_url in seen_urls:
            continue
        if not key_url and key_name in seen_names:
            continue
        if key_url:
            seen_urls.add(key_url)
        seen_names.add(key_name)
        out.append(c)
    return out
