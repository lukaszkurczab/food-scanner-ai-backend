# Compliance Ops Runbook (Data Lifecycle)

## Goal

Provide a minimal, repeatable operational process for privacy/compliance work around user data.

This runbook is implementation-focused and complements external legal documents (Terms/Privacy pages).

## Data Categories (Operational View)

- Account/profile data (`/users/me/profile`)
- Meal history and saved meals
- Chat messages
- Notifications and reminder preferences
- Feedback and attachments
- Telemetry events (if `TELEMETRY_ENABLED=true`)

## Data Export Procedure

1. User triggers data export from authenticated session.
2. Backend endpoint:
   - `GET /api/v1/users/me/export`
3. Backend returns export payload bound to token identity (never trust client-supplied `userId`).
4. If export fails, capture `X-Request-ID` and investigate backend logs + Sentry.

## Data Deletion Procedure

1. User confirms account deletion in authenticated session.
2. Backend endpoint:
   - `POST /api/v1/users/me/delete`
3. Backend removes user-owned records from primary collections/subcollections.
4. If deletion fails, retry once and escalate in Discord `launch-ops`.

## Retention & Review Cadence

1. Review retention policy quarterly (engineering + product + legal owner).
2. Review third-party processors quarterly:
   - OpenAI
   - Firebase/Google Cloud
   - Sentry
   - RevenueCat
   - Railway
3. Validate that production Terms/Privacy URLs remain publicly reachable.

## Release Evidence Packet (P0.6)

Before public launch approval, attach one evidence packet that contains:

1. telemetry retention snapshot (what is stored, where, for how long),
2. current processor matrix (service, purpose, data class, region),
3. DPA/SCC status snapshot for each external processor,
4. privacy-policy vs implementation redline status,
5. export/delete/store-disclosure links for the current RC.

## Incident Handling (Privacy-Relevant)

1. Open Discord `launch-ops` immediately and ACK within 15 minutes.
2. Freeze releases touching data pipelines.
3. Review Sentry and Railway dashboards before mitigation:
   - `https://sentry.io/organizations/<org-slug>/projects/<backend-project-slug>/`
   - `https://railway.app/project/<project-id>/service/<service-id>`
4. Capture affected scope, time window, and user impact estimate.
5. Apply feature kill-switches if needed.
6. Publish post-incident remediation tasks with owners and due dates.

## Audit Trail (Minimal)

For export/delete failures, store:

- timestamp (UTC)
- environment
- endpoint
- `X-Request-ID`
- outcome (`success` / `failed`)
- action owner
