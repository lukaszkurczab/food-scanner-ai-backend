# Backend Ops Monitoring Runbook

## Scope

This runbook defines the minimum production monitoring baseline for:

- smoke environment: `https://fitaly-backend-smoke.up.railway.app`
- production environment: `https://fitaly-backend-production.up.railway.app`

## Monitoring Baseline

1. GitHub Actions workflow `Ops Monitoring` runs every 30 minutes.
2. It checks:
   - `GET /api/v1/health` on smoke
   - `GET /api/v1/health` on production
3. It fails when:
   - HTTP status is not `200`
   - latency is over threshold
   - payload does not contain `status: "ok"` (or `healthy`)

## Latency Thresholds

- smoke health latency: `<= 3000ms`
- production health latency: `<= 2000ms`

If those thresholds fail repeatedly, treat as an incident candidate even when uptime is still present.

## Alerting Rules (Minimum)

1. `Ops Monitoring` failure on production = open incident channel.
2. 2 consecutive production failures = rollback readiness check.
3. Sentry must be enabled on production (`SENTRY_DSN`, `SENTRY_ENVIRONMENT=production`).
4. Any spike of API 5xx visible in Sentry should trigger manual investigation.

## Incident Triage Checklist

1. Confirm current deployment version on Railway.
2. Verify `GET /api/v1/health` and `GET /api/v1/version`.
3. Check latest Sentry errors and affected endpoint group.
4. Validate Firebase/OpenAI config variables are still present.
5. Apply kill-switches if needed:
   - `SMART_REMINDERS_ENABLED=false`
   - `WEEKLY_REPORTS_ENABLED=false`
   - `TELEMETRY_ENABLED=false`
6. If user impact persists, rollback to last known-good release.

## Ownership

- Primary owner: backend engineer on release duty
- Incident commander: engineering lead
