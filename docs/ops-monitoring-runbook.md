# Backend Ops Monitoring Runbook

## Scope

This runbook defines the minimum production monitoring baseline for:

- smoke environment: `https://fitaly-backend-smoke.up.railway.app`
- production environment: `https://fitaly-backend-production.up.railway.app`

## Dashboards and Links

- Ops monitoring workflow: `https://github.com/lukaszkurczab/fitaly-backend/actions/workflows/ops-monitoring.yml`
- Security workflow: `https://github.com/lukaszkurczab/fitaly-backend/actions/workflows/security.yml`
- Firestore backup workflow: `https://github.com/lukaszkurczab/fitaly-backend/actions/workflows/firestore-backup.yml`
- Firestore restore drill workflow: `https://github.com/lukaszkurczab/fitaly-backend/actions/workflows/firestore-restore-drill.yml`
- Railway production dashboard: `https://railway.app/project/<project-id>/service/<service-id>`
- Sentry backend production dashboard: `https://sentry.io/organizations/<org-slug>/projects/<backend-project-slug>/`

## Monitoring Baseline

1. GitHub Actions workflow `Ops Monitoring` runs every 30 minutes.
2. It checks:
   - `GET /api/v1/health` on smoke
   - `GET /api/v1/health` on production
   - authenticated smoke flow contracts (`scripts/check-flow-contracts.py`) when smoke secrets are configured:
     - `GET /api/v1/users/me/export`
     - `GET /api/v1/ai/credits`
     - `GET /api/v2/users/me/reports/weekly` (expected `403 WEEKLY_REPORT_PREMIUM_REQUIRED` for free smoke user)
3. It fails when:
   - HTTP status is not `200`
   - latency is over threshold
   - payload does not contain `status: "ok"` (or `healthy`)
   - or flow contract status/payload checks fail

## Latency Thresholds

- smoke health latency: `<= 3000ms`
- production health latency: `<= 2000ms`
- smoke flow contract latency (per endpoint): `<= 5000ms`

If those thresholds fail repeatedly, treat as an incident candidate even when uptime is still present.

## Alerting Rules (Minimum)

1. `Ops Monitoring` failure on production = open Discord `launch-ops`.
2. ACK SLA for production alerts during Day0-Day7 is `<= 15 minutes`.
3. 2 consecutive production failures = rollback readiness check.
4. Sentry must be enabled on production (`SENTRY_DSN`, `SENTRY_ENVIRONMENT=production`).
5. Any spike of API 5xx visible in Sentry should trigger manual investigation.
6. Workflow-level notifications are sent by `OPS_ALERT_DISCORD_WEBHOOK_URL`; GitHub email stays fallback-only.
7. If flow checks are skipped due to missing smoke secrets, treat it as monitoring debt and configure secrets immediately.

## Incident Triage Checklist

1. Confirm current deployment version on Railway.
2. Verify `GET /api/v1/health` and `GET /api/v1/version`.
3. Check latest Sentry errors and affected endpoint group in the backend production Sentry project dashboard.
4. Open the Railway backend service dashboard and confirm the active deployment, logs, and recent restart history.
5. Validate Firebase/OpenAI config variables are still present.
6. Apply kill-switches if needed:
   - `SMART_REMINDERS_ENABLED=false`
   - `WEEKLY_REPORTS_ENABLED=false`
   - `TELEMETRY_ENABLED=false`
7. If user impact persists, rollback to last known-good release.

## Ownership

- Primary owner: backend engineer on release duty
- Incident commander (Day0-Day7): engineering lead
- Day0-Day7 backend owner: backend engineer on release duty
- Day0-Day7 mobile owner: mobile engineer
