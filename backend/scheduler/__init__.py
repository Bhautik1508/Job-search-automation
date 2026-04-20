"""
APScheduler wiring for Job Search Automation.

Exposes:
    scrape_job()       — one scrape cycle (JobSpy + Apify + Instahyre).
    score_job()        — one scoring pass over all unscored jobs.
    build_scheduler()  — constructs a configured BlockingScheduler with both
                         jobs registered. Exposed so tests can inspect the
                         job table without starting the loop.

The scrape/score callables are pulled through module-level indirection
(`_run_scrape`, `_run_score`) so tests can patch them without invoking the
real scrapers or Gemini.
"""

from __future__ import annotations

import logging
import traceback
from typing import Callable

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.interval import IntervalTrigger

from backend.config import (
    SCHEDULER_TIMEZONE,
    SCORE_INTERVAL_HOURS,
    SCORE_OFFSET_MINUTES,
    SCRAPE_INTERVAL_HOURS,
)

log = logging.getLogger(__name__)


def _run_scrape() -> dict:
    """Run one full scrape cycle. Imported lazily so the scheduler module
    is cheap to import in tests."""
    from backend.scrapers.apify_scraper import ApifyScraper
    from backend.scrapers.jobspy_scraper import JobSpyScraper
    from backend.scrapers.scraper_orchestrator import ScraperOrchestrator

    orchestrator = ScraperOrchestrator(engines=[JobSpyScraper(), ApifyScraper()])
    return orchestrator.run()


def _run_score() -> dict:
    """Run one scoring pass. Imported lazily."""
    from backend.scoring.scoring_pipeline import ScoringPipeline

    pipeline = ScoringPipeline()
    if not pipeline.is_ready:
        log.warning("ScoringPipeline not ready — skipping scheduled score run.")
        return {"status": "skipped", "reason": "pipeline_not_ready"}
    return pipeline.run()


def scrape_job() -> None:
    try:
        result = _run_scrape()
        log.info("Scheduled scrape finished: %s", result)
    except Exception:
        log.exception("Scheduled scrape failed")
        traceback.print_exc()


def score_job() -> None:
    try:
        result = _run_score()
        log.info("Scheduled score finished: %s", result)
    except Exception:
        log.exception("Scheduled score failed")
        traceback.print_exc()


def build_scheduler(
    scheduler_cls: type[BlockingScheduler] = BlockingScheduler,
    scrape_func: Callable[[], None] = scrape_job,
    score_func: Callable[[], None] = score_job,
) -> BlockingScheduler:
    """
    Build a scheduler with scrape and score jobs registered.

    scheduler_cls/scrape_func/score_func are injectable so tests can pass in
    a BackgroundScheduler or stub callables without monkeypatching.
    """
    sched = scheduler_cls(timezone=SCHEDULER_TIMEZONE)
    sched.add_job(
        scrape_func,
        IntervalTrigger(hours=SCRAPE_INTERVAL_HOURS),
        id="scrape",
        name="scrape-cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    sched.add_job(
        score_func,
        IntervalTrigger(
            hours=SCORE_INTERVAL_HOURS,
            minutes=SCORE_OFFSET_MINUTES,
        ),
        id="score",
        name="score-cycle",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )
    return sched


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    sched = build_scheduler()
    log.info(
        "Scheduler starting — scrape every %sh, score every %sh+%sm, tz=%s",
        SCRAPE_INTERVAL_HOURS,
        SCORE_INTERVAL_HOURS,
        SCORE_OFFSET_MINUTES,
        SCHEDULER_TIMEZONE,
    )
    sched.start()


if __name__ == "__main__":
    main()
