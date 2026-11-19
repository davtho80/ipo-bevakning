"""Schemalagd hämtning (standard 07:05 och 16:05 svensk tid, se IPO_FETCH_TIMES)."""
from __future__ import annotations

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from . import config

log = logging.getLogger("ipo.scheduler")


def start_scheduler(job) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone=config.TZ)
    for t in config.FETCH_TIMES:
        hh, mm = (int(x) for x in t.split(":"))
        sched.add_job(job, CronTrigger(hour=hh, minute=mm, timezone=config.TZ),
                      id=f"fetch-{t}", coalesce=True, misfire_grace_time=3600)
    sched.start()
    log.info("Schemalagd hämtning kl. %s", ", ".join(config.FETCH_TIMES))
    return sched
