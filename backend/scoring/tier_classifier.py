"""
Company tier classifier (Phase 6).

Maps a company name → (tier, funding_stage, headcount_band, careers_url)
using a curated registry in data/company_tiers.json.

Lookup strategy mirrors CompanyClassifier:
  1. Exact match on normalised name.
  2. Fuzzy rapidfuzz match with threshold.
  3. Heuristic fallback: if the name carries clear "startup" signals
     (e.g. ".ai", "Labs", single-word brand) we guess `early_startup`;
     otherwise `other`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from rapidfuzz import fuzz

from backend.config import DATA_DIR
from backend.scoring.company_classifier import _normalise


TIER_ORDER = ("top_tier", "unicorn", "growth_startup", "early_startup", "other")
DEFAULT_STAGE = "unknown"
DEFAULT_HEADCOUNT = "unknown"


@dataclass(frozen=True)
class TierProfile:
    tier: str
    stage: str
    headcount: str
    careers_url: str | None
    confidence: float  # 0.0..1.0 — 1.0 for exact, fuzzy score / 100 otherwise


@lru_cache(maxsize=1)
def _load_registry() -> dict[str, TierProfile]:
    path = DATA_DIR / "company_tiers.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    out: dict[str, TierProfile] = {}
    for entry in data.get("companies", []):
        key = _normalise(entry["name"])
        if not key:
            continue
        out[key] = TierProfile(
            tier=entry.get("tier", "other"),
            stage=entry.get("stage", DEFAULT_STAGE),
            headcount=entry.get("headcount", DEFAULT_HEADCOUNT),
            careers_url=entry.get("careers_url"),
            confidence=1.0,
        )
    return out


class TierClassifier:
    """Classify a company name into a tier profile."""

    def __init__(
        self,
        registry: dict[str, TierProfile] | None = None,
        fuzzy_threshold: int = 90,
    ):
        self.registry = registry if registry is not None else _load_registry()
        self.fuzzy_threshold = fuzzy_threshold

    def classify(self, company_name: str) -> TierProfile:
        if not company_name or not company_name.strip():
            return TierProfile("other", DEFAULT_STAGE, DEFAULT_HEADCOUNT, None, 0.0)

        key = _normalise(company_name)

        exact = self.registry.get(key)
        if exact is not None:
            return exact

        best_key: str | None = None
        best_score = 0
        for reg_key in self.registry:
            score = fuzz.ratio(key, reg_key)
            if score > best_score:
                best_score = score
                best_key = reg_key

        if best_key and best_score >= self.fuzzy_threshold:
            src = self.registry[best_key]
            return TierProfile(
                tier=src.tier,
                stage=src.stage,
                headcount=src.headcount,
                careers_url=src.careers_url,
                confidence=best_score / 100.0,
            )

        # Heuristic fallback — assume early_startup for obvious startup-y names.
        if self._looks_like_startup(company_name):
            return TierProfile(
                "early_startup", DEFAULT_STAGE, DEFAULT_HEADCOUNT, None, 0.3
            )

        return TierProfile("other", DEFAULT_STAGE, DEFAULT_HEADCOUNT, None, 0.0)

    def get_tier_bonus(self, tier: str) -> float:
        """
        Additional score bonus (0-10) that biases relevancy toward higher
        quality companies. Feeds into the scoring pipeline.
        """
        return {
            "top_tier": 10.0,
            "unicorn": 7.0,
            "growth_startup": 4.0,
            "early_startup": 2.0,
            "other": 0.0,
        }.get(tier, 0.0)

    @staticmethod
    def _looks_like_startup(name: str) -> bool:
        lower = name.lower().strip()
        return any(
            tok in lower
            for tok in (".ai", ".io", " labs", " labs.", "hq", " inc.", " technologies")
        )


# ------------------------------------------------------------------
# Careers-link registry convenience (Phase 6 item #7)
# ------------------------------------------------------------------

def careers_links() -> list[dict]:
    """
    Return [{name, tier, careers_url}, ...] for every registry entry that has
    a careers_url. Used by the /api/companies/careers endpoint so the
    dashboard can surface direct-apply links without scraping.
    """
    path = DATA_DIR / "company_tiers.json"
    if not path.exists():
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    out = []
    seen: set[str] = set()
    for entry in data.get("companies", []):
        url = entry.get("careers_url")
        if not url:
            continue
        name = entry["name"]
        # Dedupe case-insensitive — Cred/CRED both listed, keep one.
        norm = _normalise(name)
        if norm in seen:
            continue
        seen.add(norm)
        out.append(
            {
                "name": name,
                "tier": entry.get("tier", "other"),
                "careers_url": url,
            }
        )
    return out
