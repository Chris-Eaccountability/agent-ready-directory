# Directory monitoring setup

Uptime, error tracking, and alert routing. Manual one-time setup — Claude
can't sign up for these on your behalf.

App on Fly: `agent-ready-directory` → `https://agent-ready-directory.fly.dev`.
Public surface: `https://directory.eaccountability.org`.

## Uptime — Better Stack (free tier, 50 monitors)

Sign up: https://betterstack.com/uptime → free plan, 1-minute check interval.

Add these monitors:

| Monitor name | URL | Method | Expected |
|---|---|---|---|
| Directory — health | `https://directory.eaccountability.org/health` | GET | 200, body contains `"status":"ok"` |
| Directory — homepage | `https://directory.eaccountability.org/` | GET | 200, content-type `text/html` |
| Directory — export.json | `https://directory.eaccountability.org/api/export.json` | GET | 200, body contains `"companies"` |

For each:
- Interval: 1 minute
- Request timeout: 10s
- Retry on failure: 3 times before alerting
- Recovery check: 1 success required to clear

## Errors — Sentry

Project name: `agent-ready-directory` (Python / FastAPI).

```bash
fly secrets set SENTRY_DSN="https://<key>@o<org>.ingest.sentry.io/<project>" \
  -a agent-ready-directory
```

The app needs to initialize `sentry-sdk` on startup. If not already wired,
add to `requirements.txt` and call `sentry_sdk.init(dsn=os.getenv("SENTRY_DSN"))`
in `app/server.py` before `app = FastAPI(...)`.

Verify: trigger a 500 (e.g. invalid POST to `/api/submissions`) and confirm
it shows up in Sentry within 30s.

## Alert routing

Pick one or more — Better Stack and Sentry support all three:

- Email — fastest setup
- SMS / phone — Better Stack escalation policies (paid tier)
- Slack — one-click integration on both Better Stack and Sentry

Recommended escalation:
1. Slack `#ops-alerts` for any monitor failure, all hours
2. SMS for `/health` failing > 5 min
3. Email digest of Sentry issues, daily 9am ET

## What this does NOT cover

- Cost monitoring. Fly bills are visible in the Fly dashboard; set a usage
  alert there.
- DNS / cert expiry. Cloudflare manages `directory.eaccountability.org`.
- Verifier accuracy regressions. Currently visible only via spot-check —
  no automated drift detection. Defer until first false positive complaint.
