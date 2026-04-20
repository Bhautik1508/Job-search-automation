"""
One-shot backfill — populate company_tier / funding_stage / headcount_band on
jobs scored before Phase 6 shipped.

Pre-Phase-6 rows have those three columns NULL even if they were otherwise
scored. This script walks every job with company set and runs the
TierClassifier over it, writing the result back. It does not touch the
relevancy score or any other field — only the three tier columns.

Run once after `alembic upgrade head`:

    python backfill_company_tiers.py
"""

from __future__ import annotations

from backend.database.models import Job, get_engine, get_session_factory, init_db
from backend.scoring.tier_classifier import TierClassifier


def main() -> None:
    engine = get_engine()
    init_db(engine)
    SessionFactory = get_session_factory(engine)
    session = SessionFactory()

    classifier = TierClassifier()

    try:
        # Only backfill rows where the tier columns are still NULL to keep
        # this script idempotent.
        stale = session.query(Job).filter(Job.company_tier.is_(None)).all()
        total = len(stale)
        print(f"📋 Backfilling tier on {total} jobs...")

        updated = 0
        by_tier: dict[str, int] = {}
        for i, job in enumerate(stale):
            if not job.company:
                continue
            profile = classifier.classify(job.company)
            job.company_tier = profile.tier
            job.funding_stage = profile.stage
            job.headcount_band = profile.headcount
            by_tier[profile.tier] = by_tier.get(profile.tier, 0) + 1
            updated += 1

            if (i + 1) % 100 == 0:
                session.commit()
                print(f"   …{i + 1}/{total}")

        session.commit()
        print(f"\n✅ Backfilled {updated}/{total} jobs")
        print("📊 Tier breakdown:")
        for tier in ("top_tier", "unicorn", "growth_startup", "early_startup", "other"):
            print(f"   {tier:<16} {by_tier.get(tier, 0)}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
