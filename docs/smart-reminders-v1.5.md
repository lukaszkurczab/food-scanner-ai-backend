# Smart Reminders v1.5 — Smarter Timing (Backend)

## What changed

v1.5 improves how the reminder decision engine selects `scheduledAtUtc` for `send` decisions.

v1 always picked the earliest viable timing candidate. v1.5 applies overlap resolution, send-now-vs-defer heuristics, and habit signal weighting to choose a more product-sensible schedule.

## What did NOT change

- Decision semantics (`send`, `suppress`, `noop`)
- Reminder kinds (`log_first_meal`, `log_next_meal`, `complete_day`)
- `ReminderDecision` API shape and contract
- Suppression logic and ordering
- Reason codes (no new codes added)
- Telemetry contract and event types
- Mobile scheduling ownership
- Preferences as hard constraints
- Rollout flags and infrastructure

## Timing heuristics

### Send now vs send later

| Condition | Decision |
|---|---|
| Preferred window open AND inside habit window | Send now (overlap) |
| Preferred window open AND near habit anchor (within 20 min) | Send now |
| Preferred window open AND far from anchor AND strong signal (7+ observed days) | Defer to habit anchor |
| Preferred window open AND far from anchor AND weak signal (< 7 days) | Send now (trust preference) |
| Preferred window not yet open AND strong signal AND habit anchor inside window | Defer to habit anchor |
| Preferred window not yet open AND weak signal | Defer to preferred window start |
| No preferred window configured | Use best habit candidate as-is |
| No valid habit candidate | Use preferred window as-is |

### Preference bounding

Habit anchors that fall outside the user's preferred window bounds are excluded from timing resolution when a preferred window exists. This ensures behavior-based timing never breaks preference hard bounds.

When no preferred candidate exists (window already passed or not configured), habit candidates are used unfiltered — preference bounds only apply when there is an active preference to bound against.

### Habit candidate selection

When multiple habit candidates exist (e.g. breakfast, lunch, dinner, snack, lastMeal for `log_next_meal`), the engine selects:

1. Among immediate candidates (currently in a habit window): the one whose anchor is closest to current time
2. If no immediate candidate: the one with the earliest future anchor

### Complete day conservative timing

`complete_day` has additional guards beyond `log_first_meal` and `log_next_meal`:

| Guard | Threshold | Purpose |
|---|---|---|
| Gate anchor | `lastMealMedianHour + 30 min` (min 18:00) | Don't consider complete_day until after typical last meal |
| Minimum meals | `expected_meals - 1` (min 1) | Day must be nearly done, not barely started |
| Signal threshold | `observedDays >= 4` | `lastMealMedianHour` must be reliable |
| Habit anchor shift | `lastMealMedianHour + 30 min` | Center habit window after last meal, not at it |

For fixture values (`lastMealMedianHour=19.0`, `expected_meals=3`):
- v1 gate: 18:00 → v1.5 gate: **19:30**
- v1 habit anchor: 19:00 → v1.5 habit anchor: **19:30**
- Minimum meals: 2 of 3

## Constants

| Constant | Value | Purpose |
|---|---|---|
| `ANCHOR_PROXIMITY_MIN` | 20 | Minutes — if within this distance of habit anchor, "close enough" for send now |
| `STRONG_HABIT_OBSERVED_DAYS` | 7 | Minimum observed days for habit signal to influence timing over preference |
| `COMPLETE_DAY_BUFFER_MIN` | 30 | Minutes after `lastMealMedianHour` before considering complete_day |
| `FIRST_MEAL_WINDOW_RADIUS_MIN` | 90 | Unchanged from v1 |
| `NEXT_MEAL_WINDOW_RADIUS_MIN` | 90 | Unchanged from v1 |
| `COMPLETE_DAY_WINDOW_RADIUS_MIN` | 120 | Unchanged from v1 |

## Architecture

All changes are inside `app/services/reminder_rule_engine.py`. No new files, no API changes, no mobile changes required.

Key functions:

| Function | Role |
|---|---|
| `_resolve_timing_plan()` | Central timing decision — overlap, defer, signal weighting |
| `_select_best_habit()` | Pick most relevant habit candidate from multiple |
| `_deferred_anchor_evaluation()` | Build deferred schedule at habit anchor within preference bounds |
| `_merge_candidates_evaluation()` | Merge overlapping preferred + habit into single evaluation |
| `_complete_day_anchor_min()` | Buffered gate for complete_day eligibility |
| `_minimum_complete_day_meals()` | Minimum meals guard for complete_day |

## What this system does NOT do

- ML or AI-based timing prediction
- Per-user model training
- Copy personalization
- Weekly report integration
- Chat context reuse
- Device push permission awareness
- Delivery confirmation-based adjustment
- DST-aware named timezone scheduling
