# Directory runbook

Three failure modes. Each section is the actual commands, no philosophy. App
name on Fly: `agent-ready-directory`. Volume mount: `/data`.

## Fly deploy fails

A new release didn't pass `/health` within `wait_timeout = 5m`.
`[experimental] auto_rollback = true` in `fly.toml` should have rolled back
to the previous release automatically.

```bash
# Confirm auto_rollback fired
fly status -a agent-ready-directory
fly releases -a agent-ready-directory | head -10

# Logs from the failed release
fly logs -a agent-ready-directory --no-tail | tail -200

# If auto_rollback didn't fire, do it by hand
fly releases -a agent-ready-directory
fly releases rollback <PREVIOUS_VERSION> -a agent-ready-directory

# After rollback, confirm
curl -s https://agent-ready-directory.fly.dev/health | jq .
```

Escalate when: rollback also fails health, or the previous release is the
broken one too. Fix forward via `fly deploy --image <known-good>`.

## Verifier pipeline returns errors

`/api/admin/verify-all` 5xxs, surface checks fail systematically, or
`oldest_check_at` in `/health` is more than 8 days old (the weekly cron
should have touched every row within the last 7).

```bash
# Confirm cron status
curl -s https://agent-ready-directory.fly.dev/health \
  | jq '{last_verifier_run_at, oldest_check_at, last_verification_run}'

# Trigger verifier manually (fires the same code path as the cron)
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://agent-ready-directory.fly.dev/api/admin/verify-all | jq .

# Check the surface_status table for systematic failures
fly ssh console -a agent-ready-directory
sqlite3 /data/directory.db "SELECT surface, COUNT(*) FROM surface_status WHERE verified=0 GROUP BY surface;"

# Inspect a specific company
sqlite3 /data/directory.db "SELECT slug, status, last_checked_at FROM companies ORDER BY last_checked_at DESC LIMIT 10;"

# Re-verify one company
curl -s -X POST -H "Authorization: Bearer $ADMIN_TOKEN" \
  https://agent-ready-directory.fly.dev/api/admin/companies/<slug>/elephant-verify

# Disable the weekly cron during incident response (backups still run)
fly secrets set DIRECTORY_VERIFIER_ENABLED=false -a agent-ready-directory
# … and re-enable
fly secrets unset DIRECTORY_VERIFIER_ENABLED -a agent-ready-directory
```

Escalate when: every verification fails (e.g. all `surface_status.verified=0`
after a fresh run). Likely a verifier bug or upstream change in target sites'
TLS/DNS. Check `app/verifier.py` for what changed.

The weekly cron fires Sunday 04:00 UTC (`verify_all_weekly` job in
`app/scheduler.py`). One hour after the nightly backup so they don't
contend for the DB.

## DB lock / corruption

Symptoms: writes hang, `sqlite3` reports `database is locked` or `malformed`,
`/health` returns `db_error`.

```bash
# 1. Stop writes during recovery
fly scale count 0 -a agent-ready-directory

# 2. SSH to volume
fly ssh console -a agent-ready-directory

# 3. Integrity check
sqlite3 /data/directory.db "PRAGMA integrity_check;"

# 4. If "ok": stale lock — restart fixes it.
#    If errors: restore from /data/backups/*.db (7 daily / 4 weekly / 6 monthly)
ls -lh /data/backups/
cp /data/directory.db /data/directory.db.broken
cp /data/backups/directory-daily-YYYY-MM-DD.db /data/directory.db

# 5. Verify
sqlite3 /data/directory.db "PRAGMA integrity_check;"
sqlite3 /data/directory.db "SELECT COUNT(*) FROM companies;"

# 6. Bring app back up
exit
fly scale count 1 -a agent-ready-directory

# 7. Confirm
curl -s https://agent-ready-directory.fly.dev/health | jq '.last_backup_at, .db_size_bytes, .counts'
```

Escalate when: every backup in `/data/backups/` also fails `integrity_check`.
Restore from a Fly volume snapshot (`fly volumes snapshots list`).

## Seed gate (production stance)

The directory must never bootstrap with un-audited rows. As of 2026-05-02
(P0 legitimacy fix), `app/seed.py` only inserts when `DEV_MODE=1` is set
in the environment. Production has no such variable, so cold boot leaves
the database empty until real submissions arrive.

```bash
# Confirm the gate is working in production
fly logs -a agent-ready-directory --no-tail | grep -i "seed"
# Expected on a cold boot:
#   Seed skipped: DEV_MODE is not set. Production-safe boot.

# Verify there is no DEV_MODE secret leaking into prod
fly secrets list -a agent-ready-directory | grep -i dev_mode
# Expected: no output. If DEV_MODE=1 appears here, unset it immediately:
fly secrets unset DEV_MODE -a agent-ready-directory
```

## Drop legacy unverified seed rows (one-time, post-P0)

Older deployments seeded 8 third-party companies (Bentley, Procore,
Autodesk, Trimble, Exodigo, Screening Eagle, Geolitix, GSSI) with
`status='verified'` despite never having gone through an EVI audit.
The migration removes them. It is idempotent and refuses to delete any
row that has a `submitted_by_email` set (i.e. a real submission).

```bash
# 1. Snapshot the DB before running
fly ssh console -a agent-ready-directory
cp /data/directory.db /data/directory.db.pre-p0

# 2. Dry run first — no changes, just a report
python /app/migrations/001_drop_unverified_seed_rows.py --dry-run

# 3. Apply
python /app/migrations/001_drop_unverified_seed_rows.py

# 4. Confirm
sqlite3 /data/directory.db "SELECT slug, name, status FROM companies;"
# Expected after migration: only elephant-accountability and
# fathom-smart-auger should remain (or fewer, if a fresh prod boot
# never ran the legacy seed).
```

If the migration shows `Skipped (real submissions): N > 0`, those slugs
have legitimate submitter emails on them and were left in place. Review
each manually before any further action.
