"""
seed.py — Initial seed data for the Agent-Ready Directory.

Production stance (post-2026-05-02 P0):
- Seed runs only when DEV_MODE=1 is set in the environment. Production
  must NEVER bootstrap the directory with rows that have not been
  through a real EVI v0.9 audit.
- The seed list is intentionally minimal: the operator's own self-sample
  (eaccountability.org) and one related-party engagement (FATHOM, with
  disclosure in the description). Both are flagged with a literal
  `[seed]` prefix in the description so the UI can never silently
  present them as organic, audited entries.
- All third-party companies that were previously seeded with
  status='verified' have been removed. Any third party shown on the
  public directory must arrive via the `submit` endpoint and pass
  through the EVI scoring pipeline before status='verified' is set.
"""

import os
import sqlite3
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# Seed data — intentionally minimal. See module docstring for stance.
# ---------------------------------------------------------------------------
SEED_COMPANIES = [
    {
        "slug": "elephant-accountability",
        "name": "Elephant Accountability",
        "domain": "eaccountability.org",
        "category": "consulting",
        "description": (
            "[seed · self-sample] Elephant Accountability LLC — operator of "
            "this directory. We run the EVI v0.9 methodology against our own "
            "domain on every release; the score on this row is our own."
        ),
        "website_url": "https://eaccountability.org",
        "elephant_verified": 1,
    },
    {
        "slug": "fathom-smart-auger",
        "name": "FATHOM / Smart Auger Technologies",
        "domain": "smartauger.tech",
        "category": "aec",
        "description": (
            "[seed · related-party engagement] AI-powered underground utility "
            "detection and mapping. Smart Auger Technologies is an active "
            "Elephant Accountability engagement; this entry is disclosed as a "
            "related-party listing, not an arms-length verification."
        ),
        "website_url": "https://smartauger.tech",
        "elephant_verified": 1,
    },
]

SURFACES = ["llms_txt", "mcp", "a2a", "ucp", "schema_org"]


def _is_dev_mode() -> bool:
    """Production stance: only seed when DEV_MODE is explicitly set to '1'."""
    return os.getenv("DEV_MODE", "0").strip() == "1"


def run_seed(conn: sqlite3.Connection) -> int:
    """
    Insert seed companies (and blank surface_status rows) if the table is
    empty AND DEV_MODE=1 is set in the environment.

    Returns the number of companies inserted (0 if already seeded OR if
    DEV_MODE is not enabled).
    """
    if not _is_dev_mode():
        return 0

    row = conn.execute("SELECT COUNT(*) as cnt FROM companies").fetchone()
    if row["cnt"] > 0:
        return 0

    now = datetime.now(timezone.utc).isoformat()

    for company in SEED_COMPANIES:
        conn.execute(
            """
            INSERT INTO companies
                (slug, name, domain, logo_url, category, description,
                 website_url, submitted_by_email, submitted_at, status,
                 elephant_verified, last_checked_at, created_at, updated_at)
            VALUES
                (:slug, :name, :domain, :logo_url, :category, :description,
                 :website_url, :submitted_by_email, :submitted_at, :status,
                 :elephant_verified, :last_checked_at, :created_at, :updated_at)
            """,
            {
                "slug": company["slug"],
                "name": company["name"],
                "domain": company["domain"],
                "logo_url": company.get("logo_url"),
                "category": company.get("category"),
                "description": company.get("description"),
                "website_url": company.get("website_url"),
                "submitted_by_email": None,
                "submitted_at": now,
                "status": "verified",
                "elephant_verified": company.get("elephant_verified", 0),
                "last_checked_at": None,
                "created_at": now,
                "updated_at": now,
            },
        )
        company_id = conn.execute(
            "SELECT id FROM companies WHERE slug = ?", (company["slug"],)
        ).fetchone()["id"]

        for surface in SURFACES:
            conn.execute(
                """
                INSERT OR IGNORE INTO surface_status
                    (company_id, surface, verified, endpoint_url,
                     last_checked_at, last_verified_at)
                VALUES (?, ?, 0, NULL, NULL, NULL)
                """,
                (company_id, surface),
            )

    conn.commit()
    return len(SEED_COMPANIES)
