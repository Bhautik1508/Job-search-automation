#!/usr/bin/env python3
"""
CLI runner — starts the APScheduler loop that runs scrape + score jobs
on a recurring interval.

Usage:
    python run_scheduler.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from backend.scheduler import main  # noqa: E402

if __name__ == "__main__":
    main()
