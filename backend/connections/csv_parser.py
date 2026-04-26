"""
Phase R4 — CSV parser for warm-connection imports.

Targets two formats out of the box:

1. LinkedIn data export (`Connections.csv`)
       First Name, Last Name, URL, Email Address, Company, Position, Connected On

2. Happenstance / generic export
       name, company, current_title, linkedin_url

We parse loosely: column names are matched case-insensitively, leading/
trailing whitespace is stripped, and any row missing a name *and* company
is dropped. Rows without `linkedin_url` still import — fuzzy company match
is what makes the referral surface useful, not the URL.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass


@dataclass
class ParsedConnection:
    name: str
    company: str
    current_title: str | None
    linkedin_url: str | None


# Column-name aliases. Order matters — first match wins.
_NAME_KEYS = ("name", "full name")
_FIRST_NAME_KEYS = ("first name", "firstname", "given name")
_LAST_NAME_KEYS = ("last name", "lastname", "surname", "family name")
_COMPANY_KEYS = ("company", "company name", "current company", "organization")
_TITLE_KEYS = ("current_title", "current title", "position", "title", "job title", "headline")
_URL_KEYS = ("linkedin_url", "linkedin url", "url", "profile url")


def _pick(row: dict[str, str], keys: tuple[str, ...]) -> str | None:
    """Return the first non-empty value matching any alias (case-insensitive)."""
    lower = {k.strip().lower(): (v or "").strip() for k, v in row.items() if k}
    for key in keys:
        v = lower.get(key)
        if v:
            return v
    return None


def parse_csv(text: str) -> tuple[list[ParsedConnection], list[str]]:
    """
    Parse a CSV string and return (connections, warnings).

    Warnings include any rows that were dropped — useful for surfacing
    "imported 184; 3 rows skipped (missing company)" in the UI.
    """
    if not text or not text.strip():
        return [], ["Empty CSV"]

    # LinkedIn's export prefixes the file with a "Notes:" preamble — skip
    # blank-and-comment lines until we find a header that looks like CSV.
    lines = text.splitlines()
    start = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "Notes:")):
            continue
        if "," in stripped:
            start = i
            break
    payload = "\n".join(lines[start:])

    reader = csv.DictReader(io.StringIO(payload))
    connections: list[ParsedConnection] = []
    warnings: list[str] = []

    if not reader.fieldnames:
        return [], ["No header row found"]

    for idx, row in enumerate(reader, start=2):  # row 1 is the header
        name = _pick(row, _NAME_KEYS)
        if not name:
            first = _pick(row, _FIRST_NAME_KEYS) or ""
            last = _pick(row, _LAST_NAME_KEYS) or ""
            name = " ".join(p for p in (first, last) if p).strip()

        company = _pick(row, _COMPANY_KEYS)
        if not name or not company:
            warnings.append(f"row {idx}: missing name or company")
            continue

        connections.append(
            ParsedConnection(
                name=name,
                company=company,
                current_title=_pick(row, _TITLE_KEYS),
                linkedin_url=_pick(row, _URL_KEYS),
            )
        )

    return connections, warnings
