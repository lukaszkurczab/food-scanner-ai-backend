# Smart Reminders v1.5 — Rollout Notes

## 1. What changed

v1.5 is a **backend-only timing improvement**. No new flags, no API changes, no mobile changes.

- `scheduledAtUtc` selection is now smarter — overlap resolution, send-now-vs-defer heuristics, habit signal weighting
- `complete_day` timing is more conservative — buffered gate, minimum meals guard, tighter signal threshold
- No new reason codes, no new suppression types, no new telemetry events

## 2. Prerequisites

Same as v1. No additional flags or infrastructure required.

v1.5 deploys as a code update behind the existing `SMART_REMINDERS_ENABLED` flag.

## 3. Verification after deploy

### 3a. Functional smoke test

```bash
# Same endpoint, same contract
curl -H "Authorization: Bearer <token>" \
  "https://<host>/api/v2/users/me/reminders/decision?day=2026-03-21&tzOffsetMin=60"

# Verify:
# - Response shape unchanged (dayKey, decision, kind, reasonCodes, scheduledAtUtc, confidence, validUntil)
# - decision values unchanged (send, suppress, noop)
# - kind values unchanged (log_first_meal, log_next_meal, complete_day)
# - reasonCodes unchanged (no new codes)
```

### 3b. Timing behavior changes to observe

For `send` decisions, `scheduledAtUtc` may now differ from v1:

| Scenario | v1 behavior | v1.5 behavior |
|---|---|---|
| Strong habit signal + far from anchor | Send at preference window start | Defer to habit anchor |
| Preference window open + habit window open | Send at preference start | Send now (overlap) |
| Habit anchor outside preference bounds | Could schedule outside bounds | Excluded from consideration |
| `complete_day` with `lastMealMedianHour=19.0` | Gate at 18:00, anchor at 19:00 | Gate at 19:30, anchor at 19:30 |

These are timing shifts, not decision changes. `send`/`suppress`/`noop` distribution should remain stable.

## 4. What to monitor

### 4a. Decision distribution (send / suppress / noop)

| Signal | Source | Healthy range | v1.5 expectation |
|---|---|---|---|
| `send` rate | Backend log `reminder.decision.computed` | 40–70% | Unchanged from v1 baseline |
| `suppress` rate | Backend log `reminder.decision.computed` | 20–40% | Unchanged — suppression logic untouched |
| `noop` rate | Backend log `reminder.decision.computed` | 5–20% | May increase slightly for `complete_day` due to stricter guards |

**If `send` rate drops > 5pp from v1 baseline**: v1.5 timing heuristics may be deferring into windows that then get suppressed. Check whether deferred `scheduledAtUtc` values land in quiet hours.

**If `noop` rate rises > 5pp from v1 baseline**: Likely `complete_day` stricter signal threshold (`observedDays >= 4` for lastMealMedianHour) or minimum meals guard filtering users who previously got `complete_day` sends. Expected for users with sparse habit data.

### 4b. `scheduledWindow` distribution

The `scheduledWindow` prop in `smart_reminder_scheduled` telemetry events reflects how far in the future the reminder was scheduled.

| Pattern | What it means |
|---|---|
| More `deferred` (> 20 min future) than v1 | Expected — strong habit signals now defer to anchor instead of sending at window start |
| Fewer `immediate` (< 5 min) than v1 | Same cause — deferral working as intended |
| `deferred` > 80% of all `send` | Possibly over-deferring — check if `STRONG_HABIT_OBSERVED_DAYS=7` threshold is too low for the user base |
| `deferred` < 5% of all `send` | Habit signals are too weak across the user base to influence timing — v1.5 is effectively a no-op |

### 4c. `schedule_failed` spikes

No v1.5-specific risk here. Same monitoring as v1.

If `schedule_failed` spikes correlate with v1.5 deploy, check whether deferred `scheduledAtUtc` values are producing timestamps that the mobile notification API rejects (e.g., scheduling too far in the future, or scheduling in the past due to timezone edge cases).

### 4d. `frequency_cap_reached` spikes

v1.5 does not change frequency cap logic. However, timing shifts could change the distribution of when `send` decisions happen within a day:

| Pattern | Concern | Action |
|---|---|---|
| `frequency_cap_reached` rate unchanged | Expected | None |
| Rate increases | Deferred sends are bunching — multiple reminders scheduling closer together | Check if habit anchors for different kinds overlap, causing 3+ sends in quick succession |
| Rate decreases | Deferred sends are spreading out more evenly | Positive signal — no action |

### 4e. `complete_day` kind distribution

`complete_day` has the most significant v1.5 behavior change. Monitor separately:

| Signal | Healthy | Investigate |
|---|---|---|
| `complete_day` as % of all `send` decisions | 10–30% | > 40% (dominance) or < 5% (disappearance) |
| `complete_day` `scheduledAtUtc` hour distribution | Centered around 19:30–21:00 for typical users | Cluster before 19:00 (buffer not working) or after 22:00 (over-delayed) |
| `complete_day` noop rate | Slightly higher than v1 | > 2x v1 rate (guards too strict) |

**If `complete_day` disappears (< 5%)**:
- Check `_minimum_complete_day_meals` — users with `expected_meals=2` need at least 1 meal, but users with `expected_meals=4` need 3. If the user base skews toward high expected meals, the guard may be too strict.
- Check `_has_reasonable_complete_day_pattern` — `observedDays >= 4` for lastMealMedianHour. If most users have < 4 observed days, `complete_day` won't fire.

**If `complete_day` dominates (> 40%)**:
- Unlikely given stricter guards. If it happens, check if `log_first_meal` and `log_next_meal` are being suppressed or deferred past their windows.

## 5. Rollback

v1.5 is a code change, not a flag change. Rollback = redeploy v1 code.

| Scenario | Action | Urgency |
|---|---|---|
| `send` rate drops > 10pp from baseline | Redeploy v1 code | Within 1 hour |
| `complete_day` disappears entirely | Redeploy v1 code | Within 4 hours |
| `frequency_cap_reached` spikes > 2x baseline | Investigate first; redeploy if bunching confirmed | Within 4 hours |
| `scheduledAtUtc` produces invalid timestamps | Redeploy v1 code | Immediate |
| Decision distribution stable, timing shifts as expected | No rollback needed | — |

### Rollback verification

After rollback to v1 code:
1. `scheduledAtUtc` values return to v1 pattern (earliest viable candidate)
2. `complete_day` gate returns to 18:00 baseline
3. No deferred-to-habit-anchor scheduling
4. Decision distribution returns to v1 baseline within 24 hours

## 6. Timeline

- **First 2 hours**: Watch decision distribution and `scheduledWindow` distribution for sharp deviations
- **First 24 hours**: Compare `complete_day` rate to v1 baseline — allow time for evening hours across all timezones
- **First 48 hours**: Establish new baseline for `scheduledWindow` (deferred vs immediate) distribution
- **After 48 hours**: Adjust alarm thresholds from v1 rollout doc if distribution has shifted

## 7. What this rollout does NOT require

- No new feature flags
- No mobile rebuild or deploy
- No Firestore schema changes
- No new telemetry events or props
- No rollout coordination with mobile team
- No per-user gradual rollout (same limitation as v1)
