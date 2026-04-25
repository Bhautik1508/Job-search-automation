"""
Portfolio proof-of-work registry — Phase 8.

A small curated set of case studies / shipped work, tagged by domain
(fintech, payments, lending, consumer, saas) and skill (growth, 0→1,
platform, data). The outreach generator picks the highest-scoring
items for a given job and inlines them into the message prompt so
Gemini has concrete, candidate-specific proof to reference.

The registry is defined in code (vs. YAML/JSON) so it ships with the
package and is trivially testable. Additions are cheap — just append
a PortfolioItem.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class PortfolioItem:
    """
    A single proof-of-work item surfaced in outreach.

    Fields are intentionally small:
      - `title` + `summary` go into the Gemini prompt.
      - `url` is an optional public link (Notion, Medium, personal site).
      - `attachment_path` points to a PDF in `backend/portfolio/` for
        email channels that prefer an attached case study. Either, both,
        or neither may be set — the generator surfaces whichever exists.
      - `domain_tags` / `skill_tags` drive ranking against a given job.
      - `metrics` is a one-line quantified impact blurb (e.g.
        "Lifted 30-day retention from 42% → 58%"). Gemini is told to
        weave it in when relevant, not paste it verbatim.
    """

    id: str
    title: str
    summary: str
    metrics: str | None = None
    url: str | None = None
    attachment_path: str | None = None
    domain_tags: tuple[str, ...] = field(default_factory=tuple)
    skill_tags: tuple[str, ...] = field(default_factory=tuple)


# Canonical tag vocabulary — kept tight so ranking is predictable.
# Adding a new tag here should be a conscious choice; ad-hoc strings
# will never match and silently get zero score.
DOMAIN_TAGS = (
    "fintech",
    "payments",
    "lending",
    "banking",
    "consumer",
    "saas",
    "b2b",
    "marketplace",
)

SKILL_TAGS = (
    "growth",
    "0-1",
    "platform",
    "data",
    "ml",
    "activation",
    "retention",
    "monetization",
    "ops",
)


# ------------------------------------------------------------------
# Default registry — edit here to add or remove case studies.
# ------------------------------------------------------------------

_DEFAULT_ITEMS: tuple[PortfolioItem, ...] = (
    PortfolioItem(
        id="upi-onboarding",
        title="Rebuilt UPI onboarding flow (payments app)",
        summary=(
            "Reduced drop-off between PAN verification and first transaction "
            "by shortening the flow from 7 steps to 3 and moving KYC checks "
            "to background polling."
        ),
        metrics="Activation +22% week-over-week; support tickets −31%.",
        attachment_path="upi-onboarding.pdf",
        domain_tags=("fintech", "payments"),
        skill_tags=("activation", "0-1"),
    ),
    PortfolioItem(
        id="lending-credit-engine",
        title="Launched personal-loan credit engine (0→1)",
        summary=(
            "Partnered with underwriting + data science to ship a new "
            "credit-scoring engine from scratch: bureau pulls, alt-data "
            "signals, approval explainability surfaced in the app."
        ),
        metrics="Approval rate +14% at unchanged default rate.",
        attachment_path="lending-credit-engine.pdf",
        domain_tags=("fintech", "lending"),
        skill_tags=("0-1", "data", "platform"),
    ),
    PortfolioItem(
        id="retention-playbook",
        title="Retention playbook for a consumer fintech app",
        summary=(
            "Built a lifecycle-based retention system: cohort analysis, "
            "triggered comms, contextual nudges on app-open. Owned the "
            "weekly retention review across PM/ENG/Data."
        ),
        metrics="30-day retention 42% → 58% over two quarters.",
        domain_tags=("fintech", "consumer"),
        skill_tags=("retention", "growth", "data"),
    ),
    PortfolioItem(
        id="merchant-platform",
        title="Merchant payments platform (B2B)",
        summary=(
            "Shipped a self-serve merchant onboarding + settlements product "
            "for mid-market SMBs. API-first architecture with dashboard UX "
            "layered on top."
        ),
        metrics="Onboarded 400+ merchants in 6 months with <1% failed-KYC.",
        domain_tags=("fintech", "payments", "b2b", "saas"),
        skill_tags=("platform", "0-1"),
    ),
    PortfolioItem(
        id="growth-experimentation",
        title="Growth-experimentation framework",
        summary=(
            "Built an in-house experiment framework covering hypothesis → "
            "design → power calc → ship → readout, adopted by 4 teams. "
            "Replaced ad-hoc toggles with tracked experiments."
        ),
        metrics="Experiment cadence +3× with better statistical hygiene.",
        domain_tags=("consumer", "saas"),
        skill_tags=("growth", "data", "platform"),
    ),
)


class PortfolioRegistry:
    """
    Small tag-based ranker over `PortfolioItem`s.

    Scoring is a bag-of-tags match — simple and predictable. The
    outreach generator pulls the top N items and lets Gemini choose
    which to mention.
    """

    def __init__(self, items: list[PortfolioItem] | None = None):
        self.items: list[PortfolioItem] = list(items if items is not None else _DEFAULT_ITEMS)

    def all_items(self) -> list[PortfolioItem]:
        return list(self.items)

    def top_matches(
        self,
        *,
        domain_tags: list[str] | None = None,
        skill_tags: list[str] | None = None,
        limit: int = 2,
    ) -> list[PortfolioItem]:
        """
        Rank items by overlap with the requested domain/skill tags.

        Domain overlap counts double — a Lending PM job should surface
        the lending case study over a generic growth one, even if
        both match on skills. Ties broken by original registry order
        (earlier items typically the strongest proofs).
        """
        want_domain = {t.lower() for t in (domain_tags or []) if t}
        want_skill = {t.lower() for t in (skill_tags or []) if t}

        scored: list[tuple[int, int, PortfolioItem]] = []
        for idx, item in enumerate(self.items):
            d_hit = len(want_domain & {t.lower() for t in item.domain_tags})
            s_hit = len(want_skill & {t.lower() for t in item.skill_tags})
            score = 2 * d_hit + s_hit
            if score > 0:
                scored.append((-score, idx, item))

        scored.sort()
        return [item for _, _, item in scored[:limit]]


def default_registry() -> PortfolioRegistry:
    """Return a registry backed by the default curated items."""
    return PortfolioRegistry()
