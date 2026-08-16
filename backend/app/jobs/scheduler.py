"""APScheduler wiring.

Cadence follows the market, not the clock: quotes refresh every minute while
the market is open and every fifteen minutes when it is not. Jobs that only
make sense after the close run once, after the close.
"""

from __future__ import annotations

import logging
from typing import Optional

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from app.core.config import settings
from app.core.market_calendar import IST, is_market_open
from app.jobs import tasks

logger = logging.getLogger(__name__)

_scheduler: Optional[BackgroundScheduler] = None


def _quote_tick() -> None:
    """One entry point, two cadences - the job itself decides how much to do."""
    if is_market_open():
        tasks.quote_refresh(limit=150)
    else:
        tasks.quote_refresh(limit=60)


def start_scheduler() -> Optional[BackgroundScheduler]:
    global _scheduler
    if not settings.enable_scheduler:
        logger.info("scheduler disabled by configuration")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone=IST)

    # Market hours: fast quote refresh, 09:15-15:30 IST on weekdays.
    scheduler.add_job(
        _quote_tick, IntervalTrigger(
            seconds=settings.quote_refresh_seconds_market_hours
        ),
        id="quote_refresh_market", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # Research call status follows quotes closely.
    scheduler.add_job(
        tasks.research_status_update,
        IntervalTrigger(minutes=5),
        id="research_status_update", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # Alerts.
    scheduler.add_job(
        tasks.alert_engine, IntervalTrigger(minutes=3),
        id="alert_engine", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # News, less often - the RSS feed does not change every minute.
    scheduler.add_job(
        tasks.news_refresh, IntervalTrigger(minutes=20),
        id="news_refresh", replace_existing=True,
        max_instances=1, coalesce=True,
    )

    # After the close.
    scheduler.add_job(
        tasks.history_refresh,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=0, timezone=IST),
        id="history_refresh", replace_existing=True,
    )
    scheduler.add_job(
        tasks.indicator_refresh,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=20, timezone=IST),
        id="indicator_refresh", replace_existing=True,
    )
    scheduler.add_job(
        tasks.end_of_day_snapshot,
        CronTrigger(day_of_week="mon-fri", hour=16, minute=40, timezone=IST),
        id="end_of_day_snapshot", replace_existing=True,
    )

    # Weekly instrument master sync.
    scheduler.add_job(
        tasks.instrument_sync,
        CronTrigger(day_of_week="sun", hour=7, minute=0, timezone=IST),
        id="instrument_sync", replace_existing=True,
    )

    scheduler.start()
    _scheduler = scheduler
    logger.info("scheduler started with %d jobs", len(scheduler.get_jobs()))
    return scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None


def scheduler_status() -> dict:
    if _scheduler is None:
        return {"running": False, "jobs": []}
    return {
        "running": _scheduler.running,
        "jobs": [
            {
                "id": job.id,
                "next_run": job.next_run_time.isoformat()
                if job.next_run_time else None,
                "trigger": str(job.trigger),
            }
            for job in _scheduler.get_jobs()
        ],
    }
