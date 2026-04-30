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
`last_verification_run` in `/health` is more than 24h old.

```bash
# Trigger verifier manually
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
```

Escalate when: every verification fails (e.g. all `surface_status.verified=0`
after a fresh run). Likely a verifier bug or upstream change in target sites'
TLS/DNS. Check `app/verifier.py` for what changed.

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
