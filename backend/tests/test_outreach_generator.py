"""
Unit tests for the outreach message generator.

The Gemini client is replaced by a stub that returns a canned JSON string
mimicking the `response.text` shape the real SDK surfaces.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from backend.database.models import Contact, Job
from backend.outreach.generator import (
    CHANNELS,
    TONES,
    OutreachGenerator,
    _tags_from_job,
    build_prompt,
)
from backend.outreach.portfolio_registry import PortfolioItem, PortfolioRegistry


class _FakeResponse:
    def __init__(self, text: str):
        self.text = text


class _FakeModels:
    def __init__(self, canned_text: str):
        self._canned = canned_text
        self.calls: list[dict] = []

    def generate_content(self, *, model, contents, config):
        self.calls.append({"model": model, "contents": contents, "config": config})
        return _FakeResponse(self._canned)


class _FakeGenai:
    def __init__(self, canned: str):
        self.models = _FakeModels(canned)


def _job(**kw) -> Job:
    defaults = dict(
        id=1,
        title="Product Manager",
        company="Razorpay",
        location="Bangalore",
        description="Lead the payments product for UPI.",
        source_portal="naukri",
        source_engine="test",
        company_type="fintech",
        company_tier="unicorn",
        skills="payments, growth, product",
        date_scraped=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    return Job(**defaults)


def _contact(**kw) -> Contact:
    defaults = dict(
        id=1,
        name="Alice Smith",
        title="Head of Product",
        company="Razorpay",
        role_type="hm",
        source_provider="apollo",
        confidence=0.9,
        last_enriched_at=datetime.now(timezone.utc),
    )
    defaults.update(kw)
    return Contact(**defaults)


class TestTagsFromJob:
    def test_fintech_maps_to_fintech_domain(self):
        domain, skills = _tags_from_job(_job(company_type="fintech", title="Growth PM"))
        assert "fintech" in domain
        assert "growth" in skills

    def test_bank_maps_to_banking(self):
        domain, _ = _tags_from_job(_job(company_type="bank"))
        assert "banking" in domain and "fintech" in domain

    def test_nbfc_maps_to_lending(self):
        domain, _ = _tags_from_job(_job(company_type="nbfc"))
        assert "lending" in domain

    def test_unknown_company_type_empty_domain(self):
        domain, _ = _tags_from_job(_job(company_type=None))
        assert domain == []


class TestBuildPrompt:
    def test_prompt_contains_key_fields(self):
        job = _job()
        contact = _contact()
        item = PortfolioItem(
            id="proof1", title="Proof", summary="Did a thing",
            metrics="10% lift", domain_tags=("fintech",),
        )
        prompt = build_prompt(
            job=job, contact=contact, channel="linkedin_note", tone="peer-pm",
            portfolio_items=[item], resume_summary="10y PM.",
        )
        assert "Razorpay" in prompt
        assert "Alice Smith" in prompt
        assert "proof1" in prompt
        assert "10y PM" in prompt
        assert "linkedin" in prompt.lower()

    def test_email_channel_requests_subject(self):
        prompt = build_prompt(
            job=_job(), contact=_contact(),
            channel="email", tone="recruiter-formal",
            portfolio_items=[], resume_summary=None,
        )
        assert "subject" in prompt.lower()

    def test_linkedin_note_no_subject(self):
        prompt = build_prompt(
            job=_job(), contact=_contact(),
            channel="linkedin_note", tone="peer-pm",
            portfolio_items=[], resume_summary=None,
        )
        assert "subject` as null" in prompt or "null" in prompt.lower()


class TestGeneratorConfiguration:
    def test_unconfigured_returns_none(self):
        gen = OutreachGenerator(api_key="", gemini_client=_FakeGenai("{}"))
        assert gen.is_configured is False
        assert gen.generate(
            job=_job(), contact=_contact(),
            channel="linkedin_note", tone="peer-pm",
        ) is None

    def test_placeholder_key_not_configured(self):
        gen = OutreachGenerator(api_key="your_gemini_api_key_here")
        assert gen.is_configured is False


class TestGenerateInvalidInputs:
    def test_unknown_channel_raises(self):
        gen = OutreachGenerator(api_key="k", gemini_client=_FakeGenai("{}"))
        with pytest.raises(ValueError):
            gen.generate(job=_job(), contact=_contact(), channel="telegram", tone="peer-pm")

    def test_unknown_tone_raises(self):
        gen = OutreachGenerator(api_key="k", gemini_client=_FakeGenai("{}"))
        with pytest.raises(ValueError):
            gen.generate(
                job=_job(), contact=_contact(),
                channel="linkedin_note", tone="casual-bro",
            )


class TestGenerateHappyPath:
    def test_produces_draft_from_gemini_output(self):
        canned = json.dumps({
            "subject": None,
            "body": "Hey Alice — loved the recent UPI launch at Razorpay. Would be great to connect.",
            "portfolio_ids_used": ["upi-onboarding"],
        })
        fake = _FakeGenai(canned)
        gen = OutreachGenerator(api_key="k", model="gemini-test", gemini_client=fake)

        result = gen.generate(
            job=_job(),
            contact=_contact(),
            channel="linkedin_note",
            tone="peer-pm",
        )
        assert result is not None
        assert result.subject is None  # linkedin_note has no subject
        assert "Alice" in result.body
        assert result.portfolio_ids_used == ["upi-onboarding"]
        assert result.model == "gemini-test"
        assert result.channel == "linkedin_note"
        assert result.tone == "peer-pm"

    def test_trims_body_to_channel_max(self):
        long_body = "x" * 500
        canned = json.dumps({
            "subject": None,
            "body": long_body,
            "portfolio_ids_used": [],
        })
        gen = OutreachGenerator(api_key="k", gemini_client=_FakeGenai(canned))
        result = gen.generate(
            job=_job(), contact=_contact(),
            channel="linkedin_note", tone="peer-pm",
        )
        # linkedin_note max is 200 chars — must be trimmed.
        assert len(result.body) <= 200

    def test_email_channel_keeps_subject(self):
        canned = json.dumps({
            "subject": "PM role at Razorpay — quick intro?",
            "body": "Hi Alice, I saw you're building out the payments team…" + "x" * 400,
            "portfolio_ids_used": [],
        })
        gen = OutreachGenerator(api_key="k", gemini_client=_FakeGenai(canned))
        result = gen.generate(
            job=_job(), contact=_contact(),
            channel="email", tone="recruiter-formal",
        )
        assert result.subject is not None
        assert "Razorpay" in result.subject

    def test_linkedin_note_drops_subject_even_if_returned(self):
        canned = json.dumps({
            "subject": "Shouldn't appear",
            "body": "Hi Alice",
            "portfolio_ids_used": [],
        })
        gen = OutreachGenerator(api_key="k", gemini_client=_FakeGenai(canned))
        result = gen.generate(
            job=_job(), contact=_contact(),
            channel="linkedin_note", tone="peer-pm",
        )
        assert result.subject is None

    def test_portfolio_items_affect_prompt(self):
        """Generator should inline the top-ranked portfolio items into the prompt."""
        canned = json.dumps({"subject": None, "body": "x", "portfolio_ids_used": []})
        fake = _FakeGenai(canned)
        my_item = PortfolioItem(
            id="custom-proof", title="Custom Proof",
            summary="Did a custom thing", metrics="5% lift",
            domain_tags=("fintech",), skill_tags=("growth",),
        )
        registry = PortfolioRegistry(items=[my_item])

        gen = OutreachGenerator(api_key="k", gemini_client=fake, registry=registry)
        gen.generate(
            job=_job(title="Growth PM"),
            contact=_contact(),
            channel="linkedin_note", tone="peer-pm",
        )
        prompt = fake.models.calls[0]["contents"]
        assert "custom-proof" in prompt
        assert "Custom Proof" in prompt


class TestAllChannelsAndTonesAccepted:
    @pytest.mark.parametrize("channel", CHANNELS)
    @pytest.mark.parametrize("tone", TONES)
    def test_every_combination_generates(self, channel, tone):
        canned = json.dumps({
            "subject": "Subj" if channel in ("email", "linkedin_inmail") else None,
            "body": "body content here — long enough to satisfy the check " * 10,
            "portfolio_ids_used": [],
        })
        gen = OutreachGenerator(api_key="k", gemini_client=_FakeGenai(canned))
        result = gen.generate(
            job=_job(), contact=_contact(),
            channel=channel, tone=tone,
        )
        assert result is not None
        assert result.channel == channel
        assert result.tone == tone
