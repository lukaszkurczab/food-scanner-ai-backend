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
4. If deletion fails, retry once and escalate to incident channel.

## Retention & Review Cadence

1. Review retention policy quarterly (engineering + product + legal owner).
2. Review third-party processors quarterly:
   - OpenAI
   - Firebase/Google Cloud
   - Sentry
   - RevenueCat
   - Railway
3. Validate that production Terms/Privacy URLs remain publicly reachable.

## Incident Handling (Privacy-Relevant)

1. Open incident channel immediately.
2. Freeze releases touching data pipelines.
3. Capture affected scope, time window, and user impact estimate.
4. Apply feature kill-switches if needed.
5. Publish post-incident remediation tasks with owners and due dates.

## Audit Trail (Minimal)

For export/delete failures, store:

- timestamp (UTC)
- environment
- endpoint
- `X-Request-ID`
- outcome (`success` / `failed`)
- action owner
