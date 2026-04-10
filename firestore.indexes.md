# Firestore Index Audit (fitaly-backend)

This document explains the composite indexes declared in `firestore.indexes.json` and maps them to the query sites in the backend service layer.

## Composite indexes included

1. `meals` (queryScope: `COLLECTION`) — `deleted ASC`, `timestamp DESC`
- Used by: `app/services/meal_service.py` (`list_history`)
- Query shape: `deleted == false` + `order_by("timestamp", DESC)` (+ document cursor pagination)

2. `meals` (queryScope: `COLLECTION`) — `deleted ASC`, `dayKey ASC`
- Used by: `app/services/habit_signal_service.py` and `app/services/nutrition_state_service.py`
- Also compatible with the same pattern in meal-domain bounded reads
- Query shape: `deleted == false` + `dayKey` range (`>=`, `<=`)

3. `meals` (queryScope: `COLLECTION`) — `deleted ASC`, `timestamp ASC`
- Used by: `app/services/habit_signal_service.py` and `app/services/nutrition_state_service.py`
- Query shape: `deleted == false` + `timestamp` range (`>=`, `<` / `<=`)

4. `telemetry_events` (queryScope: `COLLECTION`) — `userHash ASC`, `name ASC`, `ts ASC`
- Used by: `app/services/telemetry_service.py` (`count_events_for_user`)
- Query shape: `userHash == X` + `name == Y` + `ts` range (`>=`, `<=`)

5. `telemetry_events` (queryScope: `COLLECTION`) — `userHash ASC`, `ts ASC`
- Used by: `app/services/telemetry_service.py` (`get_daily_summary`, `get_smart_reminder_summary`)
- Query shape: `userHash == X` + `ts` range (`>=`, `<=`)

## De-duplication note

The `meals` indexes for `(deleted, dayKey)` and `(deleted, timestamp ASC)` are shared across:
- `meal_service` bounded timestamp/day-key filtering patterns
- `habit_signal_service` bounded reads
- `nutrition_state_service` bounded reads

Only one index entry per unique field combination is required.

## Queries verified as single-field / auto-indexed

- `app/services/meal_storage.py`
- Query: `order_by("updatedAt", ASC)` with document-id tie-break for cursoring on `users/{uid}/meals` and `users/{uid}/myMeals`
- Status: single-field index on `updatedAt` is Firestore-managed by default

- `app/services/chat_thread_service.py`
- Queries:
- `users/{uid}/chat_threads`: `order_by("updatedAt", DESC)` + `<` cursor
- `users/{uid}/chat_threads/{tid}/messages`: `order_by("createdAt", DESC)` + `<` cursor
- Status: single-field indexes (no composite required)

- `app/services/notification_plan_service.py`
- Query: `users/{uid}/meals` with `timestamp` range only (`>=`, `<=`)
- Status: single-field range on one field (no composite required)

- `app/services/streak_service.py`
- Query: `users/{uid}/meals` with `deleted == false` only
- Status: equality filter on one field (no composite required)
