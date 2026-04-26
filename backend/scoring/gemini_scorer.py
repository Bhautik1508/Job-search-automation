"""
Gemini-based relevancy scorer — sends resume + JD to Gemini and gets
a structured multi-dimensional score.

Uses the `google-genai` SDK with structured JSON output via Pydantic schemas.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from pydantic import BaseModel, Field
from google.genai import types

from backend.config import GEMINI_API_KEY, GEMINI_MODEL, GEMINI_JD_MAX_CHARS
from backend.scoring.prompts import SCORING_PROMPT


# ------------------------------------------------------------------
# Pydantic schema for structured output
# ------------------------------------------------------------------

class JobScoreResult(BaseModel):
    """Structured scoring output from Gemini."""

    skills_match: int = Field(
        ge=0, le=100,
        description="Percentage of required skills present in the resume (0-100)",
    )
    domain_fit: int = Field(
        ge=0, le=100,
        description="Alignment with fintech or banking domain (0-100)",
    )
    experience_match: int = Field(
        ge=0, le=100,
        description="Years of experience alignment (0-100)",
    )
    seniority_match: int = Field(
        ge=0, le=100,
        description="Role seniority level alignment (0-100)",
    )
    missing_skills: list[str] = Field(
        default_factory=list,
        description="Skills from JD absent in the resume (3-7 items)",
    )
    verdict: str = Field(
        description='One of: "STRONG_FIT", "GOOD_FIT", "PARTIAL_FIT", "WEAK_FIT"',
    )
    apply_priority: str = Field(
        description='One of: "APPLY_NOW", "REVIEW_FIRST", "SKIP"',
    )
    reasoning: str = Field(
        description="2-sentence explanation of the overall assessment",
    )


# ------------------------------------------------------------------
# Exceptions
# ------------------------------------------------------------------

class DailyQuotaExhausted(Exception):
    """Raised when the Gemini API daily free-tier quota is exhausted."""
    pass


# ------------------------------------------------------------------
# Scorer
# ------------------------------------------------------------------

@dataclass
class ScoringWeights:
    """Weights for the multi-dimensional scoring model."""
    skills_match: float = 0.30
    domain_fit: float = 0.25
    experience_match: float = 0.20
    seniority_match: float = 0.15
    recency: float = 0.10


# Default fallback chain — each model has its own separate daily quota
FALLBACK_MODELS = [
    "gemini-2.5-flash",
    "gemini-2.0-flash",
    "gemini-2.0-flash-lite",
    "gemini-2.5-flash-lite",
]


class GeminiScorer:
    """
    Scores job–resume fit using Google Gemini.

    Sends the resume text and job description to Gemini and receives
    structured JSON with per-dimension scores.

    Supports automatic model fallback: if one model's daily quota is
    exhausted, it switches to the next model in the chain.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        fallback_models: list[str] | None = None,
        weights: ScoringWeights | None = None,
        rpm_limit: int = 15,
    ):
        self.api_key = api_key if api_key is not None else GEMINI_API_KEY
        self.weights = weights or ScoringWeights()
        self.rpm_limit = rpm_limit
        self._request_times: list[float] = []
        self._client = None

        # Build model chain: primary model + fallbacks (deduplicated, order preserved)
        primary = model or GEMINI_MODEL
        fallbacks = fallback_models if fallback_models is not None else FALLBACK_MODELS
        seen = set()
        self._model_chain: list[str] = []
        for m in [primary] + fallbacks:
            if m not in seen:
                self._model_chain.append(m)
                seen.add(m)

        self._current_model_idx = 0
        self._exhausted_models: set[str] = set()

    @property
    def model(self) -> str:
        """Current active model."""
        return self._model_chain[self._current_model_idx]

    @property
    def is_configured(self) -> bool:
        """Check if the Gemini API key is set."""
        return bool(self.api_key) and self.api_key != "your_gemini_api_key_here"

    def _get_client(self):
        """Lazy-initialise the Gemini client."""
        if self._client is None:
            from google import genai
            self._client = genai.Client(api_key=self.api_key)
        return self._client

    def _switch_to_next_model(self) -> bool:
        """Try to switch to the next available model. Returns False if all exhausted."""
        self._exhausted_models.add(self.model)
        for i, m in enumerate(self._model_chain):
            if m not in self._exhausted_models:
                self._current_model_idx = i
                print(f"   🔄 Switching to fallback model: {self.model}")
                return True
        return False

    def _wait_for_rate_limit(self):
        """Simple rate limiter: ensure we don't exceed rpm_limit requests/minute."""
        now = time.time()
        # Remove timestamps older than 60 seconds
        self._request_times = [t for t in self._request_times if now - t < 60]

        if len(self._request_times) >= self.rpm_limit:
            # Wait until the oldest request in the window expires
            sleep_time = 60 - (now - self._request_times[0]) + 0.5
            if sleep_time > 0:
                print(f"⏳ Rate limit: waiting {sleep_time:.1f}s...")
                time.sleep(sleep_time)

        self._request_times.append(time.time())

    def score_job(
        self,
        resume_text: str,
        job_title: str,
        company: str,
        location: str,
        job_description: str,
    ) -> JobScoreResult | None:
        """
        Score a single job against the resume.

        Returns a JobScoreResult with per-dimension scores, or None on failure.
        Automatically falls back to alternate models on quota exhaustion.
        """
        if not self.is_configured:
            print("⚠️  Gemini API key not configured. Skipping scoring.")
            return None

        # Truncate long JDs to keep token cost bounded.
        jd = job_description or "No description available"
        if len(jd) > GEMINI_JD_MAX_CHARS:
            jd = jd[:GEMINI_JD_MAX_CHARS].rstrip() + "… [truncated]"

        # Build prompt
        prompt = SCORING_PROMPT.format(
            resume_text=resume_text,
            job_title=job_title,
            company=company,
            location=location or "Not specified",
            job_description=jd,
        )

        # Rate limit
        self._wait_for_rate_limit()

        max_retries = 2
        for attempt in range(max_retries + 1):
            try:
                client = self._get_client()
                response = client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_schema=JobScoreResult,
                        temperature=0.2,
                    ),
                )

                # Parse the structured response
                result = JobScoreResult.model_validate_json(response.text)
                return result

            except Exception as e:
                error_str = str(e)
                if "429" in error_str or "RESOURCE_EXHAUSTED" in error_str:
                    # Daily quota exhausted — try next model
                    if "PerDayPerProject" in error_str or "limit: 0" in error_str:
                        print(f"   ⚠️  Daily quota exhausted for '{self.model}'")
                        if self._switch_to_next_model():
                            continue  # Retry with new model immediately
                        raise DailyQuotaExhausted(
                            f"All models exhausted: {list(self._exhausted_models)}. "
                            f"Wait until tomorrow or create a new API key at "
                            f"https://aistudio.google.com/"
                        )
                    # Per-minute limit — worth retrying after a wait
                    if attempt < max_retries:
                        wait = 10 + (attempt * 10)
                        print(f"   ⏳ Rate limited — retrying in {wait}s (attempt {attempt + 1}/{max_retries})...")
                        time.sleep(wait)
                        continue
                print(f"❌ Gemini scoring failed for '{job_title}' at '{company}': {e}")
                return None

    def compute_final_score(
        self,
        score_result: JobScoreResult,
        recency_score: float = 50.0,
        domain_bonus: float = 0.0,
    ) -> float:
        """
        Compute a weighted final relevancy score (0–100).

        The domain_bonus is added on top for fintech/banking companies.
        """
        weighted = (
            score_result.skills_match * self.weights.skills_match
            + score_result.domain_fit * self.weights.domain_fit
            + score_result.experience_match * self.weights.experience_match
            + score_result.seniority_match * self.weights.seniority_match
            + recency_score * self.weights.recency
        )

        # Add domain bonus (capped at 100)
        final = min(weighted + domain_bonus, 100.0)
        return round(final, 1)

    @staticmethod
    def compute_recency_score(hours_since_posted: float | None) -> float:
        """
        Compute a recency score (0–100) based on how recently the job was posted.

        - < 6 hours: 100
        - 6–24 hours: 80–100 (linear decay)
        - 24–48 hours: 40–80 (linear decay)
        - > 48 hours: 20–40 (linear decay, floor at 20)
        - Unknown: 50
        """
        if hours_since_posted is None:
            return 50.0

        if hours_since_posted < 0:
            return 50.0  # Invalid

        if hours_since_posted <= 6:
            return 100.0
        elif hours_since_posted <= 24:
            # Linear decay from 100 to 80
            return 100.0 - (hours_since_posted - 6) * (20.0 / 18.0)
        elif hours_since_posted <= 48:
            # Linear decay from 80 to 40
            return 80.0 - (hours_since_posted - 24) * (40.0 / 24.0)
        else:
            # Slow decay from 40, floor at 20
            score = 40.0 - (hours_since_posted - 48) * (20.0 / 120.0)
            return max(score, 20.0)
