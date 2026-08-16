"""Daily scheduler (spec §13). Uses APScheduler cron with a small random jitter so
we never hit the site exactly on the hour, and a generous misfire grace so a brief
downtime doesn't skip the day's check."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import Config
from app.runner import Runner

log = logging.getLogger("flightwatcher.scheduler")


def _cron(expr: str, tz: str, jitter: int) -> CronTrigger:
    # from_crontab() doesn't take jitter; passing it to add_job with a trigger
    # *instance* silently drops it, so set it on the trigger directly.
    trigger = CronTrigger.from_crontab(expr, timezone=tz)
    trigger.jitter = jitter
    return trigger


def build_scheduler(cfg: Config, runner: Runner, promotions_job=None) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=cfg.scheduler.timezone)
    jitter = cfg.scheduler.jitter_seconds
    tz = cfg.scheduler.timezone

    sched.add_job(
        runner.refresh_all,
        trigger=_cron(cfg.scheduler.cron, tz, jitter),
        id="price_check", misfire_grace_time=3600, coalesce=True, max_instances=1,
    )
    log.info("Scheduled price_check: '%s' (%s, jitter %ds)", cfg.scheduler.cron, tz, jitter)

    if cfg.promotions.enabled and promotions_job is not None:
        sched.add_job(
            promotions_job,
            trigger=_cron(cfg.promotions.cron, tz, jitter),
            id="promotions", misfire_grace_time=3600, coalesce=True, max_instances=1,
        )
        log.info("Scheduled promotions: '%s'", cfg.promotions.cron)

    return sched
