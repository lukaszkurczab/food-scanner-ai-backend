# Smart Reminders v1 Rollout (Backend)

## Preconditions

Smart Reminders v1 requires an explicit backend flag and the existing foundation surfaces:

- `SMART_REMINDERS_ENABLED=true`
- `STATE_ENABLED=true`
- `HABITS_ENABLED=true`

Coach Insights v1 are not part of Smart Reminders v1 rollout preconditions. Reminder
runtime in v1 depends on state, habit signals already exposed through state, and
existing reminder preferences only.

Telemetry ingest is separate, but required if Smart Reminders telemetry from clients should be accepted:

- `TELEMETRY_ENABLED=true`

Before rollout, confirm:

1. `GET /api/v2/users/me/reminders/decision?day=YYYY-MM-DD` returns `200` with a valid `ReminderDecision`.
2. hard preference bounds return `decision="suppress"` with explicit suppression reason codes.
3. strong positive cases can return `decision="send"` for `log_first_meal`, `log_next_meal`, and `complete_day`.
4. weak-evidence cases return `decision="noop"` with `insufficient_signal`.
5. day-complete cases return `decision="noop"` with `day_already_complete`.
6. foundation outages produce `503`, not fake `noop`.
7. enabled reminder definitions can surface `preferred_window_open` for active windows and `preferred_window_today` for future windows later in the same day.
8. telemetry ingest accepts only the Smart Reminders allowlist fields.
9. `send` decisions carry canonical `scheduledAtUtc`, and mobile schedules from that field instead of reconstructing local time from `dayKey`.
10. meal-driven notification reconcile on mobile also re-runs Smart Reminders scheduling, so recent activity can cancel stale reminders promptly.
11. mobile emits only:
   - `smart_reminder_suppressed`
   - `smart_reminder_scheduled`
   - `smart_reminder_noop`
   - `smart_reminder_decision_failed`
   - `smart_reminder_schedule_failed`
   - generic `notification_opened` for actual reminder opens

## Observability Checklist

During staged rollout, verify:

1. `smart_reminder_scheduled` appears only for `decision="send"`.
2. `smart_reminder_suppressed` appears only for `decision="suppress"`.
3. `smart_reminder_noop` appears only for `decision="noop"`.
4. `smart_reminder_decision_failed` appears for runtime fetch/contract failures, especially `service_unavailable` and `invalid_payload`.
5. `smart_reminder_schedule_failed` appears for local execution failures such as permission-unavailable, invalid delivery time, or scheduler errors.
6. `smart_reminder_decision_computed` does not appear in telemetry summary, because it is not an allowlisted runtime event in v1.
7. `smart_reminder_opened` does not appear in telemetry summary, because reminder opens are currently observed through generic `notification_opened`.
8. `notification_opened` for smart reminders continues to carry `origin="system_notifications"` on mobile.
9. rollback by `SMART_REMINDERS_ENABLED=false` turns decision fetches into `503` instead of creating silent `noop`.

## Rollout Notes

This endpoint is a decision surface only.

- backend computes decision semantics
- backend does not schedule notifications
- backend does not send push notifications
- mobile or another consumer must treat `send` as an instruction candidate, not proof of delivery
- backend v1 does not consume Coach Insights as a reminder input
- mobile owns reminder-type scheduling only while backend decision fetch is healthy; when decision is unavailable, mobile falls back to legacy meal/day scheduling

## Suppression Behavior To Verify

During rollout, explicitly verify that these paths suppress instead of sending:

- reminders disabled
- quiet hours
- already logged recently
- recent backfill/edit activity detected

Expected effect:

- `decision="suppress"`
- explicit suppression reason code
- no fake downgrade to `noop`

Out of scope for backend v1 decision semantics:

- frequency caps without delivery history
- device permission state
- Coach Insights v1 as a reminder input

## Rollback Path

Primary rollback:

- set `SMART_REMINDERS_ENABLED=false`

Expected effect after rollback:

- `/api/v2/users/me/reminders/decision` returns `503 Service Unavailable`
- no new Smart Reminders decisions are produced
- existing notification delivery infrastructure remains untouched

Secondary rollback if reminder foundations are unstable:

- set `HABITS_ENABLED=false` or `STATE_ENABLED=false`

Expected effect:

- reminder decision API becomes unavailable with `503`
- failures stay explicit instead of surfacing as `noop`
