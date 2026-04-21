"""rq-scheduler entrypoint: registers recurring jobs on boot and ticks forever."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from rq_scheduler import Scheduler

from app.config import get_settings
from app.queue import get_redis


CRON_DAILY_FRESH = "15 */4 * * *"       # every 4 hours: pull newest page of each group
CRON_DAILY_METRICS = "30 3 * * *"       # once a day: refresh fresh posts' metrics
CRON_WEEKLY_METRICS = "45 4 * * 1"      # Monday 04:45: refresh old posts' metrics


def _ensure(scheduler: Scheduler, cron: str, func: str, queue: str, job_id: str) -> None:
    for existing in scheduler.get_jobs():
        if existing.id == job_id:
            scheduler.cancel(existing)
    scheduler.cron(
        cron,
        id=job_id,
        func=func,
        queue_name=queue,
        use_local_timezone=False,
        timeout=3600,
    )


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    cfg = get_settings()
    scheduler = Scheduler(connection=get_redis())
    _ensure(scheduler, CRON_DAILY_FRESH, "app.jobs.job_scrape_all_incremental", cfg.queue_scrape, "scrape_all_incremental")
    _ensure(scheduler, CRON_DAILY_METRICS, "app.jobs.job_refresh_metrics_fresh", cfg.queue_metrics, "refresh_metrics_fresh")
    _ensure(scheduler, CRON_WEEKLY_METRICS, "app.jobs.job_refresh_metrics_old", cfg.queue_metrics, "refresh_metrics_old")
    logging.info("Scheduled: %s", [j.id for j in scheduler.get_jobs()])
    scheduler.run()


if __name__ == "__main__":
    main()
