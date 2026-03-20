# Coach Insights v1 (Backend)

## Purpose

`GET /api/v2/users/me/coach?day=YYYY-MM-DD` is a technical surface for the Home coaching layer.

- It is derived from nutrition state and habit signals.
- It is deterministic and rule-based.
- It does not use LLM calls in the critical path.

## Response Semantics

Backend returns `CoachResponse` with:

- `dayKey`
- `computedAt`
- `source="rules"`
- `insights`
- `topInsight`
- `meta`

Interpretation rules:

- `meta.available=true` means the coach layer was computed successfully.
- `topInsight` is the selected highest-priority insight when `insights` is non-empty.
- `meta.emptyReason` is only used when `insights=[]` and `topInsight=null`.
- `meta.emptyReason="no_data"` means there is no meaningful meal data for the day.
- `meta.emptyReason="insufficient_data"` means there is some data, but not enough for a trustworthy proactive insight.
- `meta.isDegraded=true` currently indicates non-critical degradation in supporting state quality, not full coach unavailability.

## Failure Semantics

The coach endpoint is intentionally honest about infrastructure and foundation failures:

- `503 Service Unavailable`
  - `STATE_ENABLED` foundation is disabled
  - habits foundation is unavailable
  - `CoachUnavailableError` is raised from missing required coach foundations
- `500 Internal Server Error`
  - backend computation failed, including Firestore/service failures
- `200 OK`
  - only when a valid `CoachResponse` was computed

The backend should not translate these failures into `no_data` or `insufficient_data`.

## Telemetry Expectations

Backend allowlist for Coach Insights v1 telemetry:

- `coach_card_viewed`: `insightType`, `actionType`, `isPositive`
- `coach_card_expanded`: `insightType`
- `coach_card_cta_clicked`: `insightType`, `actionType`, `targetScreen`
- `coach_empty_state_viewed`: `emptyReason`

Do not send `title`, `body`, reason text, or user-authored content.

