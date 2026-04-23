"""
db.py — SQLite connection + schema management.

Uses a module-level connection so the in-memory test database is shared across
the same process. Production uses a file-backed path from DATABASE_URL env var.
"""

import os
import sqlite3
from pathlib import Path

# ---------------------------------------------------------------------------
# Database path — override with DATABASE_URL env var (e.g. for testing)
# ---------------------------------------------------------------------------
_DEFAULT_DB_PATH = os.getenv("DATABASE_URL", "/data/directory.db")

# Module-level connection (set by get_db / init_db)
_connection: sqlite3.Connection | None = None


def set_connection(conn: sqlite3.Connection) -> None:
    """Override the module-level connection (used in tests)."""
    global _connection
    _connection = conn


def get_connection() -> sqlite3.Connection:
    """Return the current module-level connection, creating it if needed."""
    global _connection
    if _connection is None:
        _connection = _open(str(_DEFAULT_DB_PATH))
    return _connection


def _open(path: str) -> sqlite3.Connection:
    """Open (or create) the SQLite database at *path*."""
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> sqlite3.Connection:
    """
    Create all tables and indexes if they don't exist.
    Returns the connection being used.
    """
    if conn is None:
        conn = get_connection()

    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY,
            slug TEXT UNIQUE NOT NULL,
            name TEXT NOT NULL,
            domain TEXT UNIQUE NOT NULL,
            logo_url TEXT,
            category TEXT,
            description TEXT,
            website_url TEXT,
            submitted_by_email TEXT,
            submitted_at TEXT NOT NULL,
            status TEXT DEFAULT 'verified',
            elephant_verified INTEGER DEFAULT 0,
            last_checked_at TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS surface_status (
            id INTEGER PRIMARY KEY,
            company_id INTEGER NOT NULL REFERENCES companies(id),
            surface TEXT NOT NULL,
            verified INTEGER DEFAULT 0,
            endpoint_url TEXT,
            last_checked_at TEXT,
            last_verified_at TEXT,
            UNIQUE (company_id, surface)
        );

        CREATE TABLE IF NOT EXISTS submissions (
            id INTEGER PRIMARY KEY,
            domain TEXT NOT NULL,
            company_name TEXT NOT NULL,
            submitted_by_email TEXT,
            category TEXT,
            submitted_at TEXT NOT NULL,
            ip_hash TEXT,
            verified INTEGER DEFAULT 0,
            company_id INTEGER REFERENCES companies(id)
        );

        CREATE INDEX IF NOT EXISTS idx_companies_category ON companies(category);
        CREATE INDEX IF NOT EXISTS idx_companies_status ON companies(status);
        CREATE INDEX IF NOT EXISTS idx_submissions_ip ON submissions(ip_hash, submitted_at);
        """
    )
    conn.commit()
    return conn


def get_db() -> sqlite3.Connection:
    """FastAPI dependency — returns an initialised connection."""
    return get_connection()
