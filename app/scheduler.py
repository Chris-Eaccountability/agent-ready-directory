"""In-process scheduler for the directory app.

Uses APScheduler's AsyncIOScheduler so jobs run inside the FastAPI event loop.
Started from app/server.py's lifespan handler.

Current jobs:
    sqlite_backup  — daily 03:00 UTC (snapshot + rotation)
    verify_all     — weekly Sunday 04:00 UTC (re-checks every company's
                     agent-discovery surfaces; the truth source behind the
                     "verified weekly" claim on directory.eaccountability.org)

Control:
    DIRECTORY_SCHEDULER_ENABLED=false disables all jobs (useful for tests).
    DIRECTORY_VERIFIER_ENABLED=false disables only the weekly verifier
        (e.g. during incidents, while keeping nightly backups running).
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Optional

log = logging.getLogger("app.scheduler")

_scheduler = None  # populated in start()

# Last completed verifier run — populated by _job_verify_all on success.
# Read by the /health endpoint so the public site can surface "last sweep"
# even before the next cron fires.
_last_verifier_run_at: Optional[str] = None
_last_verifier_count: Optional[int] = None


def _env_enabled() -> bool:
    val = os.getenv("DIRECTORY_SCHEDULER_ENABLED", "true").strip().lower()
    return val not in ("0", "false", "no", "off")


def _verifier_enabled() -> bool:
    val = os.getenv("DIRECTORY_VERIFIER_ENABLED", "true").strip().lower()
    return val not in ("0", "false", "no", "off")


def last_verifier_run() -> dict:
    """Return the most recent successful verifier run timestamp + count.

    Used by /health so the public site can show "Last weekly sweep: Xd ago"
    without a DB query (the DB last_checked_at is also exposed there as a
    fallback, but this is the cron-truth value).
    """
    return {
        "last_run_at": _last_verifier_run_at,
        "last_run_count": _last_verifier_count,
    }


async def _job_verify_all() -> None:
    """Weekly re-verification of every company's agent-discovery surfaces.

    Runs through verifier.verify_all, which iterates companies, fetches each
    surface endpoint, and updates surface_status + companies.last_checked_at.
    On success, records the run timestamp + count in module-level state so
    /health can surface the freshness without an extra DB query.
    """
    global _last_verifier_run_at, _last_verifier_count

    if not _verifier_enabled():
        log.info("verify_all: skipped (DIRECTORY_VERIFIER_ENABLED=false)")
        return

    try:
        from .db import get_connection
        from .verifier import verify_all
    except Exception:
        log.exception("verify_all: import failed")
        return

    try:
        conn = get_connection()
        log.info("verify_all: starting scheduled run")
        results = await verify_all(conn)
        _last_verifier_run_at = datetime.now(timezone.utc).isoformat()
        _last_verifier_count = len(results)
        log.info("verify_all: completed, %d companies re-checked", len(results))
    except Exception:
        log.exception("verify_all raised; swallowing so scheduler continues")


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

    # Weekly Sunday 04:00 UTC — re-verify every company's surfaces.
    # The directory advertises "Checks run weekly" on /llms.txt, in the hero
    # eyebrow, and in the company-page footnote. This cron is what makes that
    # claim true. One hour after sqlite_backup so they don't contend for the DB.
    sched.add_job(
        _job_verify_all,
        CronTrigger(day_of_week="sun", hour=4, minute=0),
        id="verify_all_weekly",
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
