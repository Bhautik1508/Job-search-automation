"""
One-shot migrator: copy rows from the local SQLite DB (data/jobs.db) into
a managed Postgres instance.

Usage:
    export DATABASE_URL="postgresql://user:pass@host:5432/dbname"
    python migrate_sqlite_to_postgres.py

Steps performed:
    1. Runs `alembic upgrade head` against the target Postgres (creates tables)
    2. Copies all Job rows (skips rows whose dedup_hash already exists)
    3. Copies all ScrapeScan rows (skips rows whose id already exists)

Safe to re-run — inserts are idempotent on dedup_hash / scan id.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

ROOT = Path(__file__).resolve().parent
SQLITE_URL = f"sqlite:///{ROOT / 'data' / 'jobs.db'}"


def main() -> int:
    target = os.getenv("DATABASE_URL")
    if not target:
        print("ERROR: DATABASE_URL not set. Example:")
        print('  export DATABASE_URL="postgresql://user:pass@host:5432/db"')
        return 1

    if target.startswith("postgres://"):
        target = target.replace("postgres://", "postgresql://", 1)
        print(f"→ Normalised scheme to postgresql:// ({target[:30]}...)")

    print(f"Source: {SQLITE_URL}")
    print(f"Target: {target[:40]}...")
    print()

    # Step 1: run alembic against the target
    print("▶ Step 1/3: running alembic upgrade head against target...")
    env = {**os.environ, "DATABASE_URL": target}
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd=str(ROOT),
        env=env,
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print("✗ Alembic failed:")
        print(result.stdout)
        print(result.stderr)
        return 1
    print(result.stdout.strip() or "(no output)")
    print("✓ Schema up to date\n")

    # Step 2: copy rows. Import models AFTER alembic so we can point at
    # the source DB without polluting module-level DATABASE_URL.
    from backend.database.models import Job, ScrapeScan

    src_engine = create_engine(SQLITE_URL)
    dst_engine = create_engine(target)

    # --- Jobs ---
    print("▶ Step 2/3: copying jobs...")
    with Session(src_engine) as src, Session(dst_engine) as dst:
        existing_hashes = {
            row[0] for row in dst.execute(select(Job.dedup_hash)).all()
        }
        print(f"  Target already has {len(existing_hashes)} jobs.")

        source_jobs = src.execute(select(Job)).scalars().all()
        print(f"  Source has {len(source_jobs)} jobs.")

        copied = 0
        skipped = 0
        for job in source_jobs:
            if job.dedup_hash in existing_hashes:
                skipped += 1
                continue
            dst.merge(_detach(job))
            copied += 1
        dst.commit()
        print(f"✓ Copied {copied} jobs ({skipped} already present)\n")

    # --- Scrape scans ---
    print("▶ Step 3/3: copying scrape scans...")
    with Session(src_engine) as src, Session(dst_engine) as dst:
        existing_ids = {
            row[0] for row in dst.execute(select(ScrapeScan.id)).all()
        }
        source_scans = src.execute(select(ScrapeScan)).scalars().all()

        copied = 0
        skipped = 0
        for scan in source_scans:
            if scan.id in existing_ids:
                skipped += 1
                continue
            dst.merge(_detach(scan))
            copied += 1
        dst.commit()
        print(f"✓ Copied {copied} scans ({skipped} already present)\n")

    print("🎉 Migration complete. You can now set DATABASE_URL on Render and redeploy.")
    return 0


def _detach(obj):
    """Return a new instance with the same column values but no session affinity."""
    cls = type(obj)
    kwargs = {
        c.name: getattr(obj, c.name)
        for c in obj.__table__.columns
    }
    return cls(**kwargs)


if __name__ == "__main__":
    sys.exit(main())
