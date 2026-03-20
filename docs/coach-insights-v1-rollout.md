# Coach Insights v1 Rollout (Backend)

## Preconditions

Coach Insights v1 has no dedicated backend flag. The endpoint depends on foundation surfaces:

- `STATE_ENABLED=true`
- `HABITS_ENABLED=true`

Telemetry ingest is separate, but required if coach telemetry from mobile should be accepted:

- `TELEMETRY_ENABLED=true`

## Verification Before Rollout

1. Verify `GET /api/v2/users/me/coach?day=YYYY-MM-DD` returns `200` with a valid `CoachResponse`.
2. Verify a no-meal day returns `meta.available=true`, `topInsight=null`, and `meta.emptyReason="no_data"`.
3. Verify a weak-evidence day returns `meta.available=true`, `topInsight=null`, and `meta.emptyReason="insufficient_data"`.
4. Verify foundation outages produce `503`, not fake empty reasons.
5. Verify telemetry batch ingest accepts only the coach allowlist fields.

## Rollback Behavior

Rollback uses foundation flags, not a coach-specific switch:

- Set `STATE_ENABLED=false` to disable the coach technical surface together with state consumption.
- Or set `HABITS_ENABLED=false` to make the coach surface unavailable while keeping other v2 surfaces as configured.

Expected effect after rollback:

- `/api/v2/users/me/coach` returns `503 Service Unavailable`
- mobile falls back to non-coach Home rendering
- no new live coach payloads are produced

