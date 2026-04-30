"""SQLite snapshot backup with rotation.

Runs nightly at 03:00 UTC via app/scheduler.py::_job_sqlite_backup. Snapshots
the active DB to /data/backups/ on the same Fly volume using SQLite's
``.backup`` API (consistent point-in-time copy, safe with WAL writers).

Rotation policy:
  daily   — keep last 7
  weekly  — keep last 4 (Sundays)
  monthly — keep last 6 (1st of month)

Out of scope: shipping snapshots off the Fly volume. That requires an
external storage target (S3/R2) and is a follow-up. Whole-volume disaster
is covered by Fly volume snapshots; this covers DB-level mistakes.
"""
from __future__ import annotations

import logging
import os
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("app.sqlite_backup")

DEFAULT_BACKUP_DIR = "/data/backups"
DAILY_KEEP = 7
WEEKLY_KEEP = 4
MONTHLY_KEEP = 6


def _resolve_db_path() -> str:
    """Mirror app/db.py: DATABASE_URL env or /data/directory.db."""
    return os.getenv("DATABASE_URL", "/data/directory.db")


def _backup_dir() -> Path:
    return Path(os.getenv("DIRECTORY_BACKUP_DIR", DEFAULT_BACKUP_DIR))


def _classify(date: datetime) -> str:
    if date.day == 1:
        return "monthly"
    if date.weekday() == 6:  # Sunday
        return "weekly"
    return "daily"


def _filename(now: datetime, db_name: str) -> str:
    tier = _classify(now)
    stamp = now.strftime("%Y-%m-%d")
    return f"{db_name}-{tier}-{stamp}.db"


def backup_now(db_path: str | None = None,
               backup_dir: str | None = None) -> dict[str, Any]:
    """Run a backup. Returns a status dict; never raises into caller."""
    db = db_path or _resolve_db_path()
    out_dir = Path(backup_dir) if backup_dir else _backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)

    now = datetime.now(timezone.utc)
    db_name = Path(db).stem
    out_path = out_dir / _filename(now, db_name)

    started = datetime.now(timezone.utc)
    try:
        src = sqlite3.connect(db)
        try:
            dst = sqlite3.connect(str(out_path))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except Exception as exc:
        log.exception("sqlite backup failed: %s", exc)
        return {
            "ok": False,
            "error": f"{exc.__class__.__name__}: {exc}",
            "db_path": db,
            "started_at": started.isoformat(),
        }

    finished = datetime.now(timezone.utc)
    size = out_path.stat().st_size if out_path.exists() else 0
    duration_ms = int((finished - started).total_seconds() * 1000)

    rotated = rotate(out_dir, db_name)

    return {
        "ok": True,
        "db_path": db,
        "backup_path": str(out_path),
        "size_bytes": size,
        "tier": _classify(now),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "duration_ms": duration_ms,
        "rotated": rotated,
    }


def rotate(backup_dir: Path | None = None,
           db_name: str | None = None) -> dict[str, list[str]]:
    """Apply the retention policy. Returns {tier: [removed_filenames]}."""
    out_dir = backup_dir or _backup_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    db_stem = db_name or Path(_resolve_db_path()).stem
    removed: dict[str, list[str]] = {"daily": [], "weekly": [], "monthly": []}

    for tier, keep in (
        ("daily", DAILY_KEEP),
        ("weekly", WEEKLY_KEEP),
        ("monthly", MONTHLY_KEEP),
    ):
        prefix = f"{db_stem}-{tier}-"
        files = sorted(
            (p for p in out_dir.iterdir() if p.name.startswith(prefix)),
            reverse=True,
        )
        for old in files[keep:]:
            try:
                old.unlink()
                removed[tier].append(old.name)
            except OSError as exc:
                log.warning("rotate: failed to remove %s: %s", old, exc)

    return removed


@dataclass
class BackupStatus:
    last_backup_at: str | None
    last_backup_size_bytes: int | None
    last_backup_tier: str | None
    backup_count: int
    backup_dir: str
    total_bytes: int


def status() -> dict[str, Any]:
    """Read filesystem state for /health. Cheap; safe to call per request."""
    out_dir = _backup_dir()
    if not out_dir.exists():
        return asdict(BackupStatus(
            last_backup_at=None, last_backup_size_bytes=None,
            last_backup_tier=None, backup_count=0,
            backup_dir=str(out_dir), total_bytes=0,
        ))
    files = [p for p in out_dir.iterdir() if p.is_file() and p.suffix == ".db"]
    if not files:
        return asdict(BackupStatus(
            last_backup_at=None, last_backup_size_bytes=None,
            last_backup_tier=None, backup_count=0,
            backup_dir=str(out_dir), total_bytes=0,
        ))
    latest = max(files, key=lambda p: p.stat().st_mtime)
    st = latest.stat()
    last_at = datetime.fromtimestamp(st.st_mtime, tz=timezone.utc).isoformat()

    parts = latest.stem.split("-")
    tier = parts[-4] if len(parts) >= 4 and parts[-4] in ("daily", "weekly", "monthly") else None

    total = sum(p.stat().st_size for p in files)
    return asdict(BackupStatus(
        last_backup_at=last_at,
        last_backup_size_bytes=st.st_size,
        last_backup_tier=tier,
        backup_count=len(files),
        backup_dir=str(out_dir),
        total_bytes=total,
    ))
