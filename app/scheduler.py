"""In-process scheduler for the directory app.

Uses APScheduler's AsyncIOScheduler so jobs run inside the FastAPI event loop.
Started from app/server.py's lifespan handler.

Current jobs:
    sqlite_backup  — daily 03:00 UTC (snapshot + rotation)

Control:
    DIRECTORY_SCHEDULER_ENABLED=false disables all jobs (useful for tests).
"""
from __future__ import annotations

import logging
import os
from typing import Optional

log = logging.getLogger("app.scheduler")

_scheduler = None  # populated in start()


def _env_enabled() -> bool:
    val = os.getenv("DIRECTORY_SCHEDULER_ENABLED", "true").strip().lower()
    return val not in ("0", "false", "no", "off")


def _job_sqlite_backup() -> None:
    """Nightly SQLite snapshot to /data/backups/ (Week 1 ops hardening).

    On-volume only. Off-volume shipping is a follow-up. See
    app/sqlite_backup.py for retention policy (7 daily / 4 weekly / 6 monthly).
    """
    try:
        from .sqlite_backup import backup_now
    except Exception as exc:
        log.exception("sqlite_backup import failed: %s", exc)
        return

    try:
        result = backup_now()
    except Exception:
        log.exception("sqlite_backup.backup_now raised; swallowing")
        return

    if result.get("ok"):
        log.info(
            "sqlite_backup: ok tier=%s size=%d bytes path=%s",
            result.get("tier"), result.get("size_bytes", 0), result.get("backup_path"),
        )
    else:
        log.error("sqlite_backup: FAILED — %s", result.get("error"))


def start() -> Optional[object]:
    """Start the APScheduler instance. Returns the scheduler (or None if disabled)."""
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    if not _env_enabled():
        log.info("scheduler disabled via DIRECTORY_SCHEDULER_ENABLED")
        return None

    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from apscheduler.triggers.cron import CronTrigger
    except ImportError:
        log.warning("apscheduler not installed; scheduler disabled")
        return None

    sched = AsyncIOScheduler(timezone="UTC")

    sched.add_job(
        _job_sqlite_backup,
        CronTrigger(hour=3, minute=0),
        id="sqlite_backup_daily",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    sched.start()
    _scheduler = sched
    log.info("scheduler started: %d job(s)", len(sched.get_jobs()))
    return sched


def stop() -> None:
    """Stop the scheduler if running. Safe to call when not started."""
    global _scheduler
    if _scheduler is None:
        return
    try:
        _scheduler.shutdown(wait=False)
    except Exception:
        log.exception("scheduler shutdown raised; ignoring")
    _scheduler = None
