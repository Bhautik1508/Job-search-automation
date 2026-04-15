"""
Deduplication engine — prevents the same job from being stored multiple times
even when scraped from different portals or engines.

Strategy:
  1. Compute a fuzzy "dedup hash" from (normalised_title + normalised_company + normalised_location).
  2. Before inserting, check the DB for an existing hash.
  3. Cross-engine fuzzy matching catches near-duplicates
     (e.g. "Sr. Product Manager" on Naukri vs "Senior Product Manager" on LinkedIn).
"""

from __future__ import annotations

import hashlib
import re

from rapidfuzz import fuzz

from backend.scrapers.base_scraper import RawJob


# ------------------------------------------------------------------
# Normalisation helpers
# ------------------------------------------------------------------

def _normalise(text: str | None) -> str:
    """Lowercase, strip, collapse whitespace, remove common noise."""
    if not text:
        return ""
    text = text.lower().strip()
    # Remove special chars but keep alphanumeric and spaces
    text = re.sub(r"[^\w\s]", " ", text)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


_TITLE_SYNONYMS = {
    "sr.": "senior",
    "sr ": "senior ",
    "jr.": "junior",
    "jr ": "junior ",
    "mgr": "manager",
    "prod.": "product",
    "prod ": "product ",
    "assoc.": "associate",
    "assoc ": "associate ",
    "vp": "vice president",
}


def _normalise_title(title: str) -> str:
    """Extra normalisation for job titles — expand common abbreviations."""
    n = _normalise(title)
    for abbr, full in _TITLE_SYNONYMS.items():
        n = n.replace(abbr, full)
    return n


# ------------------------------------------------------------------
# Hash computation
# ------------------------------------------------------------------

def compute_dedup_hash(job: RawJob) -> str:
    """
    Produce a deterministic SHA-256 hash from the job's key identity fields.
    Two jobs with the same hash are considered duplicates.
    """
    title = _normalise_title(job.title)
    company = _normalise(job.company)
    location = _normalise(job.location or "")

    composite = f"{title}||{company}||{location}"
    return hashlib.sha256(composite.encode("utf-8")).hexdigest()


# ------------------------------------------------------------------
# Fuzzy deduplication within a batch
# ------------------------------------------------------------------

def deduplicate_jobs(jobs: list[RawJob], similarity_threshold: int = 85) -> list[RawJob]:
    """
    Remove near-duplicate jobs from a list using fuzzy matching.

    Jobs are compared on (title + company). If the fuzzy ratio
    exceeds `similarity_threshold`, the later job is dropped.

    Returns a new list with duplicates removed (first occurrence kept).
    """
    if not jobs:
        return []

    unique: list[RawJob] = []
    seen_keys: list[str] = []

    for job in jobs:
        key = f"{_normalise_title(job.title)} @ {_normalise(job.company)}"

        is_dup = False
        for existing_key in seen_keys:
            if fuzz.ratio(key, existing_key) >= similarity_threshold:
                is_dup = True
                break

        if not is_dup:
            unique.append(job)
            seen_keys.append(key)

    return unique
