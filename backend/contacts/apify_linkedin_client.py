"""
Apify LinkedIn profile-scraper fallback — Phase 7.5.

Uses an Apify Actor (default: `harvestapi~linkedin-profile-scraper`) to
search LinkedIn for people at a given company by role keyword. We treat
this as a fallback when Apollo returns nothing — LinkedIn scraping is
slower and noisier, but surfaces profiles Apollo hasn't indexed yet.

Items returned by the actor vary in shape across community actors. We
accept the common key variants and skip entries that can't be mapped.
"""

from __future__ import annotations

from typing import Any

from backend.config import (
    APIFY_API_TOKEN,
    APIFY_LINKEDIN_PROFILE_ACTOR,
    CONTACT_HM_TITLE_KEYWORDS,
    CONTACT_RECRUITER_TITLE_KEYWORDS,
    hm_titles_for_job,
)
from backend.contacts.apollo_client import ApolloContact, _classify_role, _classify_confidence


class ApifyLinkedInClient:
    """
    Wrapper around the Apify Actor-run API for LinkedIn profile discovery.

    Dependency-inject the apify client in tests:
        ApifyLinkedInClient(api_client=fake_client)

    The injected client is expected to expose `.actor(id).call(run_input=...)`
    and the run response to expose `.dataset().list_items().items`.
    """

    def __init__(
        self,
        api_token: str | None = None,
        actor_id: str | None = None,
        max_items: int = 10,
        api_client=None,
    ):
        self.api_token = api_token if api_token is not None else APIFY_API_TOKEN
        self.actor_id = actor_id or APIFY_LINKEDIN_PROFILE_ACTOR
        self.max_items = max_items
        self._injected_client = api_client

    @property
    def is_configured(self) -> bool:
        return bool(self.api_token) and bool(self.actor_id)

    def search_people_at_company(
        self,
        company: str,
        *,
        per_role_limit: int = 5,
        job_title: str | None = None,
    ) -> list[ApolloContact]:
        """
        Run one actor call per role_type (HM + recruiter), combining the
        top keyword from each bucket into the search query.

        When ``job_title`` is set, the HM keyword is the first entry of
        the job's role-specific list (e.g. "engineering manager" for an
        SDE role) instead of the default "head of product".

        Falls through to [] on any failure — the pipeline treats "no
        fallback contacts" as a skippable outcome, not a fatal error.
        """
        if not self.is_configured:
            return []

        client = self._get_client()
        if client is None:
            return []

        hm_titles = hm_titles_for_job(job_title)
        hm_keyword = hm_titles[0] if hm_titles else (
            CONTACT_HM_TITLE_KEYWORDS[0] if CONTACT_HM_TITLE_KEYWORDS else "head of product"
        )
        contacts: list[ApolloContact] = []
        for role_type, keyword in (
            ("hm", hm_keyword),
            ("recruiter", CONTACT_RECRUITER_TITLE_KEYWORDS[0] if CONTACT_RECRUITER_TITLE_KEYWORDS else "recruiter"),
        ):
            items = self._run_actor(client, company=company, keyword=keyword, limit=per_role_limit)
            for item in items:
                contact = self._item_to_contact(
                    item, company=company, expected_role=role_type
                )
                if contact is not None:
                    contacts.append(contact)
        return contacts

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _get_client(self):
        """Lazy-import apify-client so test environments without it can still load."""
        if self._injected_client is not None:
            return self._injected_client
        try:
            from apify_client import ApifyClient  # noqa: WPS433 — lazy import is intentional
        except ImportError as e:
            print(f"[apify-linkedin] apify-client not installed: {e}")
            return None
        return ApifyClient(self.api_token)

    def _run_actor(
        self,
        client,
        *,
        company: str,
        keyword: str,
        limit: int,
    ) -> list[dict[str, Any]]:
        """Invoke the actor and collect the dataset items."""
        run_input = {
            "searchQueries": [f"{keyword} {company}"],
            "maxItems": limit,
            # Backward-compat keys some community actors accept.
            "company": company,
            "keyword": keyword,
            "limit": limit,
        }
        try:
            run = client.actor(self.actor_id).call(run_input=run_input)
        except Exception as e:  # noqa: BLE001 — log + skip; it's a fallback
            print(f"[apify-linkedin] actor call failed for {company}: {e}")
            return []

        if not run:
            return []

        dataset_id = run.get("defaultDatasetId") if isinstance(run, dict) else None
        try:
            if dataset_id:
                dataset = client.dataset(dataset_id)
            else:
                # Some harnesses return a run object with `.dataset()` helper.
                dataset = run.dataset() if hasattr(run, "dataset") else None
            if dataset is None:
                return []
            listing = dataset.list_items()
            items = getattr(listing, "items", None)
            if items is None and isinstance(listing, dict):
                items = listing.get("items")
            return list(items or [])
        except Exception as e:  # noqa: BLE001
            print(f"[apify-linkedin] failed to fetch dataset: {e}")
            return []

    @staticmethod
    def _item_to_contact(
        item: dict[str, Any],
        *,
        company: str,
        expected_role: str,
    ) -> ApolloContact | None:
        """
        Map a LinkedIn profile dict to an ApolloContact.

        Community actors use different key names — we try a few synonyms
        for each field, falling back to None when the essentials (name)
        are missing.
        """
        if not isinstance(item, dict):
            return None

        name = (
            item.get("fullName")
            or item.get("name")
            or " ".join(
                [
                    (item.get("firstName") or "").strip(),
                    (item.get("lastName") or "").strip(),
                ]
            ).strip()
        )
        name = (name or "").strip()
        if not name:
            return None

        title = (
            item.get("headline")
            or item.get("title")
            or item.get("occupation")
        )
        linkedin_url = (
            item.get("profileUrl")
            or item.get("linkedinUrl")
            or item.get("url")
            or item.get("linkedin")
        )
        email = item.get("email")

        role_type = _classify_role(title, fallback=expected_role)
        # LinkedIn scrapes give us no delivery-confidence signal, so we
        # anchor confidence on title-match alone, then down-weight it
        # slightly because LinkedIn data is stale more often than Apollo's.
        base_confidence = _classify_confidence(title, expected_role)
        confidence = round(base_confidence * 0.8, 2)

        return ApolloContact(
            name=name,
            title=title,
            company=company,
            linkedin_url=linkedin_url,
            email=email,
            role_type=role_type,
            confidence=confidence,
            raw=item,
        )
