# Smart Reminders v1 (Backend)

## Purpose

`GET /api/v2/users/me/reminders/decision?day=YYYY-MM-DD` is a technical surface for backend-owned reminder decision semantics.

- It is deterministic and rule-based.
- It consumes nutrition state, habit semantics, reminder preferences, and current-time context.
- It does not send, schedule, or deliver notifications.
- It does not use AI in the critical path.
- Coach Insights v1 are not a runtime dependency of Smart Reminders v1.

Current runtime scope is intentionally small:

- `NutritionState`
- habit signals already embedded in `NutritionState`
- existing reminder / notification preferences
- current-time context derived for reminder evaluation

Out of scope for Smart Reminders v1 runtime:

- Coach Insights v1 as a reminder input
- coach-card-derived reminder orchestration
- coach-assisted reminder scoring

## Decision Semantics

Backend returns `ReminderDecision` with:

- `dayKey`
- `computedAt`
- `decision`
- `kind`
- `reasonCodes`
- `scheduledAtUtc`
- `confidence`
- `validUntil`

Interpretation rules:

- `decision="send"` means a reminder is product-sensible for the current day and evaluation context.
- `decision="suppress"` means a reminder opportunity existed, but a hard bound blocked it.
- `decision="noop"` means there is no credible reminder opportunity for the current state.
- `kind` is present only for `send`.
- `scheduledAtUtc` is present only for `send`.
- `scheduledAtUtc` is the canonical scheduling source of truth for consumers.
- `scheduledAtUtc` may be either immediate or later today, depending on the chosen reminder window.
- `reasonCodes` are ordered and deterministic.
- `preferred_window_open` is emitted only when the current local time falls inside
  a runtime-derived preference window built from enabled notification definitions.
- `preferred_window_today` is emitted when the preferred reminder window exists later
  in the current local day.
- `habit_window_today` is emitted when habit timing yields a future reminder window
  later in the current local day.

## Suppression Semantics

`suppress` is reserved for hard constraints and recent-activity guards, not for weak evidence.

Current v1 suppression reasons include:

- `reminders_disabled`
- `quiet_hours`
- `already_logged_recently`
- `recent_activity_detected`

Interpretation rules:

- preferences and quiet hours have priority over behavior heuristics
- recent activity suppresses reminder sends instead of allowing duplicate nudges
- `recent_activity_detected` means a meal record changed recently, but not through a
  just-logged current meal that would already trigger `already_logged_recently`
- suppression is not equivalent to “nothing interesting happened”

Current v1 does **not** claim backend support for:

- delivery frequency caps
- device push-permission state

Those concerns are outside the backend decision contract today and are not
expressed as reminder reason codes in v1.

## Noop vs Suppress vs Failure

- `noop`
  - the system computed successfully
  - there is no trustworthy reminder opportunity for the current day
  - typical reason: `insufficient_signal` or `day_already_complete`
- `suppress`
  - the system computed successfully
  - a reminder candidate is being intentionally blocked
  - typical reason: preferences, quiet hours, recent activity
- `failure`
  - the system did not compute a valid reminder decision
  - backend must return an explicit HTTP failure, not `noop`

The backend must not translate infrastructure or foundation failures into `noop` or `suppress`.

## Failure Semantics

The reminder endpoint is intentionally honest about missing foundations and infrastructure errors:

- `503 Service Unavailable`
  - `SMART_REMINDERS_ENABLED=false`
  - `STATE_ENABLED=false`
  - habits foundation is unavailable for reminder semantics
  - `ReminderUnavailableError` is raised from missing required reminder foundations
- `500 Internal Server Error`
  - backend computation failed, including Firestore/service failures
- `200 OK`
  - only when a valid `ReminderDecision` was computed

## Telemetry Expectations

Backend allowlist for Smart Reminders v1 telemetry:

- `smart_reminder_suppressed`: `decision`, `suppressionReason`, `confidenceBucket`
- `smart_reminder_scheduled`: `reminderKind`, `decision`, `confidenceBucket`, `scheduledWindow`
- `smart_reminder_noop`: `decision`, `noopReason`, `confidenceBucket`
- `smart_reminder_decision_failed`: `failureReason`
- `smart_reminder_schedule_failed`: `reminderKind`, `decision`, `confidenceBucket`, `failureReason`

Intentional exclusions in v1:

- `smart_reminder_decision_computed` is not ingested today because no runtime currently emits it
- `smart_reminder_opened` is not allowlisted on the backend yet
- `smart_reminder_suppressed` does not carry `reminderKind`, because suppress decisions do not declare `kind`

Telemetry rules:

- keep Smart Reminder values enum-bounded, not just key-bounded
- do not send reminder copy
- do not send full reason-code arrays
- do not send user-authored content
- do not send health, meal, or profile details
- keep props categorical and bounded
