#!/usr/bin/env python3
"""
CLI runner — kicks off a full scrape cycle.

Usage:
    python run_scrape.py                    # Full scrape (JobSpy + Apify)
    python run_scrape.py --engine jobspy    # Only JobSpy
    python run_scrape.py --engine apify     # Only Apify
"""

import argparse
import json
import sys

# Allow running from project root
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from backend.scrapers.scraper_orchestrator import ScraperOrchestrator
from backend.scrapers.jobspy_scraper import JobSpyScraper
from backend.scrapers.apify_scraper import ApifyScraper


def main():
    parser = argparse.ArgumentParser(description="Job Search Automation — Scrape Runner")
    parser.add_argument(
        "--engine",
        choices=["all", "jobspy", "apify"],
        default="all",
        help="Which scraping engine(s) to run (default: all)",
    )
    parser.add_argument(
        "--search",
        type=str,
        default=None,
        help="Override search term (default: uses SEARCH_VARIANTS from config)",
    )
    parser.add_argument(
        "--location",
        type=str,
        default=None,
        help="Override location (default: uses TARGET_CITIES from config)",
    )
    args = parser.parse_args()

    # Build engine list
    engines = []
    if args.engine in ("all", "jobspy"):
        engines.append(JobSpyScraper())
    if args.engine in ("all", "apify"):
        engines.append(ApifyScraper())

    # Optional overrides
    search_terms = [args.search] if args.search else None
    locations = [args.location] if args.location else None

    print("=" * 60)
    print("🚀 Job Search Automation — Scrape Runner")
    print("=" * 60)
    print(f"Engines : {[e.engine_name for e in engines]}")
    print(f"Terms   : {search_terms or 'config defaults'}")
    print(f"Cities  : {locations or 'config defaults'}")
    print("=" * 60)

    orchestrator = ScraperOrchestrator(
        engines=engines,
        search_terms=search_terms,
        locations=locations,
    )

    result = orchestrator.run()

    print("\n" + "=" * 60)
    print("📊 Scrape Summary")
    print("=" * 60)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
