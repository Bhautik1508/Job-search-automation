"""
Company classifier — determines whether a company operates in the
fintech, banking, or NBFC space.

Two-tier strategy:
  1. **Curated list lookup** (fast, free) — checks against 200+ fintech
     companies and 50+ banks/NBFCs from JSON data files.
  2. **Gemini LLM fallback** (slower, uses API quota) — for companies
     not found in the curated lists.

The classification result is one of:
  "fintech", "bank", "nbfc", "digital_banking_arm", "other"
"""

from __future__ import annotations

import json
from pathlib import Path
from functools import lru_cache

from rapidfuzz import fuzz

from backend.config import DATA_DIR


# ------------------------------------------------------------------
# Curated list loading
# ------------------------------------------------------------------

@lru_cache(maxsize=1)
def _load_fintech_companies() -> dict[str, str]:
    """
    Load fintech companies → {normalised_name: sub_domain}.
    """
    path = DATA_DIR / "fintech_companies.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return {
        _normalise(c["name"]): c.get("sub_domain", "fintech")
        for c in data.get("companies", [])
    }


@lru_cache(maxsize=1)
def _load_banking_companies() -> dict[str, str]:
    """
    Load banks, NBFCs, digital banking arms → {normalised_name: type}.
    """
    path = DATA_DIR / "banking_companies.json"
    if not path.exists():
        return {}
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    result: dict[str, str] = {}
    for bank in data.get("banks", []):
        result[_normalise(bank["name"])] = "bank"
    for nbfc in data.get("nbfcs", []):
        result[_normalise(nbfc["name"])] = "nbfc"
    for arm in data.get("digital_banking_arms", []):
        result[_normalise(arm["name"])] = "digital_banking_arm"
    return result


# ------------------------------------------------------------------
# Normalisation
# ------------------------------------------------------------------

def _normalise(name: str) -> str:
    """Lowercase, strip, remove common suffixes like Ltd, Pvt, Inc, etc."""
    if not name:
        return ""
    n = name.lower().strip()
    for suffix in (" ltd", " ltd.", " pvt", " pvt.", " private limited",
                   " limited", " inc", " inc.", " corporation", " corp",
                   " corp.", " technologies", " solutions", " services",
                   " india", " financial"):
        if n.endswith(suffix):
            n = n[: -len(suffix)].strip()
    return n


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------

class CompanyClassifier:
    """
    Classify companies as fintech, bank, nbfc, digital_banking_arm, or other.

    Uses curated lists with fuzzy matching. An optional Gemini LLM fallback
    can be provided for unknown companies.
    """

    def __init__(
        self,
        fintech_map: dict[str, str] | None = None,
        banking_map: dict[str, str] | None = None,
        fuzzy_threshold: int = 85,
    ):
        self.fintech_map = fintech_map if fintech_map is not None else _load_fintech_companies()
        self.banking_map = banking_map if banking_map is not None else _load_banking_companies()
        self.fuzzy_threshold = fuzzy_threshold

    def classify(self, company_name: str) -> tuple[str, float]:
        """
        Classify a company name.

        Returns (company_type, confidence):
          - company_type: "fintech" | "bank" | "nbfc" | "digital_banking_arm" | "other"
          - confidence: 0.0–1.0 (1.0 = exact match in curated list)
        """
        if not company_name or not company_name.strip():
            return "other", 0.0

        normalised = _normalise(company_name)

        # 1. Exact match in fintech list
        if normalised in self.fintech_map:
            return "fintech", 1.0

        # 2. Exact match in banking list
        if normalised in self.banking_map:
            return self.banking_map[normalised], 1.0

        # 3. Fuzzy match against fintech list
        best_type, best_score = self._fuzzy_search(normalised, self.fintech_map, "fintech")

        # 4. Fuzzy match against banking list
        bank_type, bank_score = self._fuzzy_search(normalised, self.banking_map)
        if bank_score > best_score:
            best_type, best_score = bank_type, bank_score

        if best_score >= self.fuzzy_threshold:
            return best_type, best_score / 100.0

        # 5. Keyword heuristic — look for domain keywords in the company name
        keyword_type = self._keyword_heuristic(company_name)
        if keyword_type != "other":
            return keyword_type, 0.6

        return "other", 0.0

    def classify_batch(self, company_names: list[str]) -> list[tuple[str, float]]:
        """Classify a list of company names."""
        return [self.classify(name) for name in company_names]

    def get_domain_bonus(self, company_type: str) -> float:
        """
        Return a domain bonus score (0–15) based on company type.
        Fintech and banking companies get a bonus to boost relevancy.
        """
        bonuses = {
            "fintech": 15.0,
            "bank": 12.0,
            "nbfc": 10.0,
            "digital_banking_arm": 13.0,
            "other": 0.0,
        }
        return bonuses.get(company_type, 0.0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fuzzy_search(
        self,
        normalised: str,
        lookup: dict[str, str],
        default_type: str | None = None,
    ) -> tuple[str, int]:
        """Find the best fuzzy match in a lookup dict. Returns (type, score)."""
        best_score = 0
        best_type = "other"

        for key, ctype in lookup.items():
            score = fuzz.ratio(normalised, key)
            if score > best_score:
                best_score = score
                best_type = ctype if default_type is None else default_type

        return best_type, best_score

    @staticmethod
    def _keyword_heuristic(company_name: str) -> str:
        """Check for fintech/banking keywords in the company name."""
        lower = company_name.lower()

        fintech_keywords = [
            "fintech", "pay ", "payment", "wallet", "lending",
            "credit", "loan", "insurance", "insure", " fi ",
            "finance tech", "neobank", "neo-bank", "money",
        ]
        bank_keywords = [
            " bank", "banking",
        ]
        nbfc_keywords = [
            "nbfc", "housing finance", "home finance", "micro finance",
            "microfinance",
        ]

        for kw in bank_keywords:
            if kw in lower:
                return "bank"
        for kw in nbfc_keywords:
            if kw in lower:
                return "nbfc"
        for kw in fintech_keywords:
            if kw in lower:
                return "fintech"

        return "other"
