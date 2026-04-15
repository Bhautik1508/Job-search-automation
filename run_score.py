#!/usr/bin/env python3
"""
CLI runner — scores unscored jobs in the database using Gemini.

Usage:
    python run_score.py                        # Score all unscored jobs
    python run_score.py --limit 20             # Score at most 20 jobs
    python run_score.py --resume path/to/cv.pdf  # Use a specific resume
"""

import argparse
import json
import sys

# Allow running from project root
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))

from backend.scoring.scoring_pipeline import ScoringPipeline
from backend.config import RESUME_PATH


def main():
    parser = argparse.ArgumentParser(description="Job Search Automation — Scoring Runner")
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Maximum number of jobs to score (default: all unscored)",
    )
    parser.add_argument(
        "--resume",
        type=str,
        default=None,
        help=f"Path to resume PDF (default: {RESUME_PATH})",
    )
    args = parser.parse_args()

    resume = args.resume or RESUME_PATH

    print("=" * 60)
    print("🎯 Job Search Automation — Scoring Runner")
    print("=" * 60)
    print(f"Resume  : {resume}")
    print(f"Limit   : {args.limit or 'all unscored'}")
    print("=" * 60)

    pipeline = ScoringPipeline(resume_path=resume)

    if not pipeline.is_ready:
        if not pipeline.resume_text:
            print("\n❌ No resume found. Provide a PDF resume:")
            print(f"   • Place it at: {RESUME_PATH}")
            print(f"   • Or use: python run_score.py --resume path/to/cv.pdf")
        if not pipeline.scorer.is_configured:
            print("\n❌ Gemini API key not configured.")
            print("   Set GEMINI_API_KEY in your .env file.")
            sys.exit(1)

    result = pipeline.run(limit=args.limit)

    print("\n" + "=" * 60)
    print("📊 Scoring Summary")
    print("=" * 60)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
