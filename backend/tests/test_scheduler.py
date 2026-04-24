"""
Unit tests for the APScheduler wiring.

We never start the scheduler loop — `build_scheduler` returns an unstarted
scheduler and we inspect its job table to verify intervals, IDs, and that
scrape/score stubs run safely when triggered directly.
"""

from __future__ import annotations

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

from backend import scheduler as sched_mod


class TestBuildScheduler:
    def test_registers_all_jobs(self):
        sched = sched_mod.build_scheduler(scheduler_cls=BackgroundScheduler)
        job_ids = {j.id for j in sched.get_jobs()}
        assert job_ids == {"scrape", "score", "enrich"}

    def test_scrape_uses_configured_interval(self, monkeypatch):
        monkeypatch.setattr(sched_mod, "SCRAPE_INTERVAL_HOURS", 7)
        sched = sched_mod.build_scheduler(scheduler_cls=BackgroundScheduler)
        scrape = sched.get_job("scrape")
        assert scrape.trigger.interval.total_seconds() == 7 * 3600

    def test_score_has_offset(self, monkeypatch):
        monkeypatch.setattr(sched_mod, "SCORE_INTERVAL_HOURS", 4)
        monkeypatch.setattr(sched_mod, "SCORE_OFFSET_MINUTES", 15)
        sched = sched_mod.build_scheduler(scheduler_cls=BackgroundScheduler)
        score = sched.get_job("score")
        assert score.trigger.interval.total_seconds() == 4 * 3600 + 15 * 60

    def test_enrich_uses_configured_interval(self, monkeypatch):
        monkeypatch.setattr(sched_mod, "ENRICH_INTERVAL_HOURS", 24)
        monkeypatch.setattr(sched_mod, "ENRICH_OFFSET_MINUTES", 90)
        sched = sched_mod.build_scheduler(scheduler_cls=BackgroundScheduler)
        enrich = sched.get_job("enrich")
        assert enrich.trigger.interval.total_seconds() == 24 * 3600 + 90 * 60

    def test_max_instances_one(self):
        """Jobs must not pile up if a run takes longer than the interval."""
        sched = sched_mod.build_scheduler(scheduler_cls=BackgroundScheduler)
        for job_id in ("scrape", "score", "enrich"):
            job = sched.get_job(job_id)
            assert job.max_instances == 1
            assert job.coalesce is True

    def test_injected_callables_are_wired(self):
        called = {"scrape": 0, "score": 0, "enrich": 0}

        def fake_scrape():
            called["scrape"] += 1

        def fake_score():
            called["score"] += 1

        def fake_enrich():
            called["enrich"] += 1

        sched = sched_mod.build_scheduler(
            scheduler_cls=BackgroundScheduler,
            scrape_func=fake_scrape,
            score_func=fake_score,
            enrich_func=fake_enrich,
        )
        # Invoke the job funcs directly — no need to start the scheduler.
        sched.get_job("scrape").func()
        sched.get_job("score").func()
        sched.get_job("enrich").func()
        assert called == {"scrape": 1, "score": 1, "enrich": 1}


class TestJobCallables:
    def test_scrape_job_swallows_exceptions(self, monkeypatch):
        """A crashing scrape must not take the scheduler loop down."""
        def boom():
            raise RuntimeError("network down")

        monkeypatch.setattr(sched_mod, "_run_scrape", boom)
        sched_mod.scrape_job()  # should not raise

    def test_score_job_swallows_exceptions(self, monkeypatch):
        def boom():
            raise RuntimeError("gemini 500")

        monkeypatch.setattr(sched_mod, "_run_score", boom)
        sched_mod.score_job()  # should not raise

    def test_enrich_job_swallows_exceptions(self, monkeypatch):
        def boom():
            raise RuntimeError("apollo 500")

        monkeypatch.setattr(sched_mod, "_run_enrich", boom)
        sched_mod.enrich_job()  # should not raise

    def test_scrape_job_logs_success(self, monkeypatch):
        """
        Verify scrape_job logs the orchestrator result. We intercept the
        module's logger directly rather than relying on caplog — other
        tests in the suite reconfigure root logging, which makes caplog
        flaky for this specific logger.
        """
        monkeypatch.setattr(sched_mod, "_run_scrape", lambda: {"new_inserted": 3})

        captured: list[tuple[str, tuple]] = []

        class _StubLogger:
            def info(self, msg, *args, **kwargs):
                captured.append((msg, args))

            def exception(self, *a, **kw):
                pass

        monkeypatch.setattr(sched_mod, "log", _StubLogger())
        sched_mod.scrape_job()

        assert captured, "scrape_job should have logged"
        rendered = [msg % args if args else msg for msg, args in captured]
        assert any("new_inserted" in r for r in rendered)
