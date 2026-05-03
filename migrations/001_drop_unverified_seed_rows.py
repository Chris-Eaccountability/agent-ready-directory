#!/usr/bin/env python3
"""
migrations/001_drop_unverified_seed_rows.py

P0 legitimacy migration (2026-05-02). Removes the 8 third-party companies
that were previously bootstrapped into the production directory database
with `status='verified'` despite never having gone through an EVI v0.9
audit.

The two retained rows — Elephant Accountability (self-sample) and
FATHOM / Smart Auger (related-party engagement) — both carry a literal
`[seed]` prefix in their description so the UI can never silently
present them as organic, audited entries.

Usage (from inside the container with the SQLite volume mounted):

    # Dry run — show what would be deleted, change nothing.
    python migrations/001_drop_unverified_seed_rows.py --dry-run

    # Actually delete.
    python migrations/001_drop_unverified_seed_rows.py

    # Custom DB path (defaults to $DATABASE_URL or /data/directory.db).
    python migrations/001_drop_unverified_seed_rows.py --db /path/to/directory.db

The script is idempotent: re-running after a successful migration is a
no-op (each target row is checked individually, and missing rows are
reported as "already removed", not as errors).
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone

# The 8 third-party slugs that were seeded as `status='verified'` in the
# pre-P0 seed.py. These have never been through an EVI audit and must
# not appear on the public directory.
TARGET_SLUGS = [
    "bentley-systems",
    "exodigo",
    "procore",
    "autodesk",
    "trimble",
    "screening-eagle",
    "geolitix",
    "gssi",
]


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Drop unverified seed rows from the directory database."
    )
    parser.add_argument(
        "--db",
        default=os.getenv("DATABASE_URL", "/data/directory.db"),
        help="Path to the SQLite directory database (default: $DATABASE_URL or /data/directory.db).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would change without executing any DELETE.",
    )
    args = parser.parse_args()

    db_path = args.db
    if not os.path.exists(db_path):
        print(f"ERROR: database not found at {db_path}", file=sys.stderr)
        return 2

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    print(f"Database: {db_path}")
    print(f"Mode: {'DRY RUN' if args.dry_run else 'EXECUTE'}")
    print(f"Started: {datetime.now(timezone.utc).isoformat()}")
    print(f"Target slugs ({len(TARGET_SLUGS)}):")
    for slug in TARGET_SLUGS:
        print(f"  - {slug}")
    print()

    deleted_companies = 0
    deleted_surfaces = 0
    skipped = 0
    skipped_non_seed = 0

    for slug in TARGET_SLUGS:
        row = conn.execute(
            """
            SELECT id, slug, name, status, submitted_by_email, created_at
            FROM companies
            WHERE slug = ?
            """,
            (slug,),
        ).fetchone()

        if row is None:
            print(f"  [{slug}] not present — already removed.")
            skipped += 1
            continue

        # Defensive: only delete rows that look like seed rows (no submitter email).
        # If a real submission later took the same slug, refuse to delete it.
        if row["submitted_by_email"] is not None:
            print(
                f"  [{slug}] SKIPPED — has submitted_by_email={row['submitted_by_email']!r}; "
                f"this is a real submission, not a seed row. Not touching."
            )
            skipped_non_seed += 1
            continue

        company_id = row["id"]
        surface_count = conn.execute(
            "SELECT COUNT(*) AS cnt FROM surface_status WHERE company_id = ?",
            (company_id,),
        ).fetchone()["cnt"]

        print(
            f"  [{slug}] FOUND id={company_id} status={row['status']!r} "
            f"surfaces={surface_count} created_at={row['created_at']}"
        )

        if not args.dry_run:
            conn.execute(
                "DELETE FROM surface_status WHERE company_id = ?", (company_id,)
            )
            conn.execute("DELETE FROM companies WHERE id = ?", (company_id,))
            deleted_companies += 1
            deleted_surfaces += surface_count
        else:
            deleted_companies += 1
            deleted_surfaces += surface_count

    if not args.dry_run:
        conn.commit()

    conn.close()

    print()
    print("Summary")
    print(f"  Companies {'would be ' if args.dry_run else ''}deleted: {deleted_companies}")
    print(f"  Surface rows {'would be ' if args.dry_run else ''}deleted: {deleted_surfaces}")
    print(f"  Already removed: {skipped}")
    print(f"  Skipped (real submissions): {skipped_non_seed}")
    print(f"Finished: {datetime.now(timezone.utc).isoformat()}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
