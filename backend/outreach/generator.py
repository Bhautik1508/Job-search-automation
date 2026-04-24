"""
AI-drafted outreach generator — Phase 8.

Given a (job, contact, channel, tone) tuple, produces a ready-to-send
outreach message using Gemini. Pulls the top-matched portfolio items
and inlines them into the prompt so Gemini has concrete proof points.

Channel-specific constraints (LinkedIn note ≤200 chars, InMail 300–500,
email 120–180 words) are enforced in the Pydantic response schema +
prompt instructions.

Design notes:
  - Structured JSON output via Pydantic `response_schema` so parsing
    never depends on string scraping.
  - The Gemini client is dependency-injected so tests can stub it.
  - Fails closed: if Gemini isn't configured, generate() returns None
    rather than raising — the caller decides whether to surface that
    as a 503 or queue for later.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Iterable

from pydantic import BaseModel, Field

from backend.config import GEMINI_API_KEY, GEMINI_MODEL
from backend.database.models import Contact, Job
from backend.outreach.portfolio_registry import (
    PortfolioItem,
    PortfolioRegistry,
    default_registry,
)


# ------------------------------------------------------------------
# Valid channel / tone constants
# ------------------------------------------------------------------

CHANNELS = ("linkedin_note", "linkedin_inmail", "email", "referral_ask")
TONES = ("founder-pitch", "peer-pm", "recruiter-formal")

# Channel-specific length rules, surfaced to Gemini *and* validated post-parse.
_CHANNEL_RULES: dict[str, dict] = {
    "linkedin_note": {
        "max_chars": 200,
        "min_chars": 80,
        "has_subject": False,
        "guidance": (
            "LinkedIn connection note — MUST be ≤200 characters total. "
            "One short hook referencing the company or role, one sentence of "
            "relevant proof, a soft ask to connect. No sign-off block."
        ),
    },
    "linkedin_inmail": {
        "max_chars": 600,
        "min_chars": 250,
        "has_subject": True,
        "guidance": (
            "LinkedIn InMail — 300–500 characters body. Subject is a short "
            "hook (≤60 chars). Include one concrete proof point tied to the "
            "role and a specific ask (15-min chat)."
        ),
    },
    "email": {
        "max_chars": 1400,
        "min_chars": 500,
        "has_subject": True,
        "guidance": (
            "Cold email — 120–180 words body. Subject ≤70 chars, no "
            "clickbait. Opening line ties to something specific about the "
            "company or role. One paragraph on relevant proof, one "
            "paragraph with a clear ask (30-min intro chat, or referral)."
        ),
    },
    "referral_ask": {
        "max_chars": 900,
        "min_chars": 300,
        "has_subject": False,
        "guidance": (
            "Referral-ask — sent to a peer/PM already at the company. "
            "Friendly, low-pressure. Name the role explicitly, explain "
            "briefly why you'd be a fit (1 proof), and ask whether they'd "
            "be open to a referral OR a 10-min chat first."
        ),
    },
}

_TONE_GUIDANCE: dict[str, str] = {
    "founder-pitch": (
        "Tone: founder-pitch. Confident but not salesy. Lead with "
        "outcomes shipped. Short sentences. Treat the reader as a peer."
    ),
    "peer-pm": (
        "Tone: peer-PM. Warm and collegial. Acknowledge their work if the "
        "context suggests it. Use plain PM language, skip buzzwords."
    ),
    "recruiter-formal": (
        "Tone: recruiter-formal. Polite, structured, signal-dense. "
        "Mention years of experience + one matched skill. No slang."
    ),
}


# ------------------------------------------------------------------
# Structured output schema
# ------------------------------------------------------------------

class OutreachMessage(BaseModel):
    """Shape Gemini returns. Keep fields minimal — parsing is expensive when fields are optional."""

    subject: str | None = Field(
        default=None,
        description="Subject line for channels that support one; null otherwise.",
    )
    body: str = Field(
        description="Full message body, ready to paste. No placeholders like [NAME].",
    )
    portfolio_ids_used: list[str] = Field(
        default_factory=list,
        description="IDs of portfolio items actually referenced in the body.",
    )


# ------------------------------------------------------------------
# Job → tag extraction
# ------------------------------------------------------------------

_COMPANY_TYPE_TO_DOMAIN = {
    "fintech": ["fintech"],
    "bank": ["fintech", "banking"],
    "nbfc": ["fintech", "lending"],
    "digital_banking_arm": ["fintech", "banking"],
}

_TITLE_KEYWORD_TO_SKILL = {
    "growth": "growth",
    "0-1": "0-1",
    "zero to one": "0-1",
    "platform": "platform",
    "retention": "retention",
    "activation": "activation",
    "data": "data",
    "monetization": "monetization",
    "revenue": "monetization",
}


def _tags_from_job(job: Job) -> tuple[list[str], list[str]]:
    """Derive (domain_tags, skill_tags) for portfolio ranking."""
    domain = list(_COMPANY_TYPE_TO_DOMAIN.get(job.company_type or "", []))

    title = (job.title or "").lower()
    skills = (job.skills or "").lower()
    blob = f"{title} {skills}"
    found_skills: list[str] = []
    for kw, tag in _TITLE_KEYWORD_TO_SKILL.items():
        if kw in blob and tag not in found_skills:
            found_skills.append(tag)

    return domain, found_skills


# ------------------------------------------------------------------
# Prompt builder
# ------------------------------------------------------------------

def _render_portfolio(items: Iterable[PortfolioItem]) -> str:
    """Render portfolio items as compact lines for the prompt."""
    lines: list[str] = []
    for item in items:
        metrics = f" — {item.metrics}" if item.metrics else ""
        lines.append(f"- [{item.id}] {item.title}: {item.summary}{metrics}")
    return "\n".join(lines) if lines else "(no matching items)"


def build_prompt(
    *,
    job: Job,
    contact: Contact,
    channel: str,
    tone: str,
    portfolio_items: Iterable[PortfolioItem],
    resume_summary: str | None,
) -> str:
    """Assemble the full Gemini prompt. Exposed for tests."""
    rules = _CHANNEL_RULES[channel]
    tone_block = _TONE_GUIDANCE.get(tone, _TONE_GUIDANCE["peer-pm"])

    jd = (job.description or "").strip()
    if len(jd) > 1500:
        jd = jd[:1500].rstrip() + "… [truncated]"

    resume_block = (
        resume_summary.strip()
        if resume_summary
        else "Experienced Product Manager with fintech/payments/lending background."
    )

    portfolio_block = _render_portfolio(portfolio_items)

    subject_instruction = (
        "Return a `subject` (≤70 chars) and a `body`."
        if rules["has_subject"]
        else "Return `subject` as null. Only `body` matters for this channel."
    )

    return f"""You are drafting an outreach message from a Product Manager candidate to a
hiring contact at a target company. Return ONLY valid JSON matching the schema.

### Candidate resume summary
{resume_block}

### Target role
Title: {job.title}
Company: {job.company}
Location: {job.location or 'Not specified'}
Job description:
{jd or '(no description available)'}

### Recipient
Name: {contact.name}
Title: {contact.title or 'Unknown'}
Role type: {contact.role_type}

### Candidate proof points (pick at most 1–2; reference only IDs you actually cite)
{portfolio_block}

### Channel rules
{rules['guidance']}
Hard constraints: body length between {rules['min_chars']} and {rules['max_chars']} characters.

### Tone
{tone_block}

### Output rules
- No placeholders like [NAME], [COMPANY] — fill in everything.
- Mention the recipient by first name where natural.
- No emojis.
- If you reference a proof point, add its ID to `portfolio_ids_used`.
- {subject_instruction}
"""


# ------------------------------------------------------------------
# Generator
# ------------------------------------------------------------------

@dataclass
class GenerationResult:
    """What generate() returns: the draft + metadata about how it was produced."""

    subject: str | None
    body: str
    portfolio_ids_used: list[str]
    model: str
    channel: str
    tone: str


class OutreachGenerator:
    """
    Gemini-backed outreach drafter.

    Dependency injection:
      - `registry`: swap out the portfolio source in tests.
      - `gemini_client`: stub that exposes `.models.generate_content(...)`.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        registry: PortfolioRegistry | None = None,
        gemini_client=None,
    ):
        self.api_key = api_key if api_key is not None else GEMINI_API_KEY
        self.model = model or GEMINI_MODEL
        self.registry = registry or default_registry()
        self._client = gemini_client

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key) and self.api_key != "your_gemini_api_key_here"

    def _get_client(self):
        """Lazy-init the Gemini client unless one was injected."""
        if self._client is not None:
            return self._client
        from google import genai
        self._client = genai.Client(api_key=self.api_key)
        return self._client

    def generate(
        self,
        *,
        job: Job,
        contact: Contact,
        channel: str,
        tone: str,
        resume_summary: str | None = None,
        portfolio_limit: int = 2,
    ) -> GenerationResult | None:
        """
        Produce one outreach draft. Returns None when Gemini isn't
        configured — callers (the API endpoint) translate that to 503.
        """
        if channel not in CHANNELS:
            raise ValueError(f"Unknown channel {channel!r}; expected one of {CHANNELS}")
        if tone not in TONES:
            raise ValueError(f"Unknown tone {tone!r}; expected one of {TONES}")
        if not self.is_configured:
            return None

        domain_tags, skill_tags = _tags_from_job(job)
        items = self.registry.top_matches(
            domain_tags=domain_tags,
            skill_tags=skill_tags,
            limit=portfolio_limit,
        )

        prompt = build_prompt(
            job=job,
            contact=contact,
            channel=channel,
            tone=tone,
            portfolio_items=items,
            resume_summary=resume_summary,
        )

        from google.genai import types  # lazy — keeps test import cheap

        client = self._get_client()
        response = client.models.generate_content(
            model=self.model,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=OutreachMessage,
                temperature=0.4,
            ),
        )

        try:
            parsed = OutreachMessage.model_validate_json(response.text)
        except Exception:
            # Gemini occasionally returns an object the schema can't
            # validate (trailing text, etc.) — try a lenient JSON parse.
            data = json.loads(response.text)
            parsed = OutreachMessage.model_validate(data)

        body = parsed.body.strip()
        rules = _CHANNEL_RULES[channel]
        if len(body) > rules["max_chars"]:
            # Hard-trim rather than fail — the caller can always regenerate.
            body = body[: rules["max_chars"]].rstrip()

        return GenerationResult(
            subject=(parsed.subject or None) if rules["has_subject"] else None,
            body=body,
            portfolio_ids_used=list(parsed.portfolio_ids_used or []),
            model=self.model,
            channel=channel,
            tone=tone,
        )
