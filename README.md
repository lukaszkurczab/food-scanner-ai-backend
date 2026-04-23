# Fitaly Backend (FastAPI)

This repository is proprietary and is not licensed for public use, redistribution, or modification.

## Purpose

This backend provides the API layer for the Fitaly mobile app.
It owns AI execution for the mobile app and exposes the server-side entrypoints used by chat, meal text analysis, photo analysis, logging, and supporting integrations.

## Tech Stack

- Python 3.11+
- FastAPI (API framework)
- Uvicorn + Gunicorn (ASGI server, local + production)
- Firestore Python client (`google-cloud-firestore`)
- Firebase Admin SDK (`firebase-admin`)
- OpenAI SDK (`openai`)
- Pydantic Settings + `python-dotenv` (configuration)
- Sentry SDK (error monitoring)
- Pytest + HTTPX (tests)
- Pyright (static type checking)

## Project Structure

```text
app/
  main.py                # FastAPI app factory
  api/
    router.py            # version router registry (/api/v1, /api/v2)
    v1/                  # stable API version 1
    v2/                  # next API version (extension point)
  core/                  # app config/settings
  services/              # business logic
  schemas/               # request/response models
  db/                    # database integration layer
tests/
requirements.txt
README.md
```

## API Versioning Strategy

- Keep existing contracts in `v1` stable (`/api/v1/...`).
- Introduce breaking changes only in `v2` (`/api/v2/...`).
- Add new `v2` endpoints in `app/api/v2/endpoints/*` and register them in `app/api/v2/router.py` without modifying `v1` handlers.

## AI Architecture Boundary (v1 vs v2)

- **Canonical AI Chat v2**
  - Endpoint: `app/api/v2/endpoints/ai_chat.py` (`POST /api/v2/ai/chat/runs`)
  - Orchestration: `app/domain/chat/*`
  - Deterministic tools: `app/domain/tools/*`
  - Chat memory + runs persistence: `app/domain/chat_memory/*`, `app/domain/ai_runs/*`, `app/infra/firestore/repositories/*`
  - Schemas: `app/schemas/ai_chat/*`
- **Legacy AI v1 analysis (kept for compatibility)**
  - Route: `app/api/routes/ai.py` (photo/text meal analysis only)
  - Supporting services: `ai_gateway_service`, `ai_gateway_logger`, `openai_service`, `text_meal_service`
- **Removed legacy chat v1**
  - Legacy v1 ask endpoint and its chat-only helper modules were removed.
  - Reintroduction of chat compatibility aliases/patch points is disallowed.
- Detailed v2 architecture note: [AI Chat v2 Architecture](./docs/ai-chat-v2-architecture.md)

## AI Chat v2 (Ready For Simulator)

- Canonical endpoint for new chat: `POST /api/v2/ai/chat/runs`
- Auth: `Authorization: Bearer <firebase-id-token>`
- Request contract (camelCase):
  - `threadId`
  - `clientMessageId`
  - `message`
  - `language` (`pl` or `en`)
- Response contract (camelCase):
  - `runId`
  - `assistantMessageId`
  - `reply`
  - `usage`
  - `contextStats`
- Errors use stable shape:
  - `detail.code`
  - `detail.message`

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/v2/ai/chat/runs \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{
    "threadId": "thread-mobile-1",
    "clientMessageId": "client-msg-1",
    "message": "Podsumuj moje dzisiejsze makro.",
    "language": "pl"
  }'
```

Notes:
- v2 adoption is controlled by API path (`/api/v2/...`), not by a dedicated runtime feature flag.
- v1 analysis endpoints remain stable (`photo/text-meal/credits`), while chat is v2-only.

## Local Run

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

If the repo was moved and `.venv` points to an old interpreter path, recreate it before running tests:

```bash
rm -rf .venv
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Health check:

```text
GET http://127.0.0.1:8000/api/v1/health
```

Run tests:

```bash
pytest -q
```

Run only canonical AI Chat v2 tests:

```bash
pytest -q -m ai_v2 app/tests
```

Run only legacy AI v1 compatibility tests:

```bash
pytest -q -m legacy_ai tests
```

Run type checking:

```bash
./.venv/bin/pyright
```

## Foundation services overview

The backend exposes two API versions:

**v1 (stable)** — original endpoints, no feature flags:

- `GET /api/v1/health`
- `GET /api/v1/version`
- `POST /api/v1/ai/photo/analyze` — photo meal analysis
- `POST /api/v1/ai/text-meal/analyze` — text meal analysis
- `GET /api/v1/ai/credits` — credit balance
- `POST /api/v1/users/me/meals` — meal upsert
- `POST /api/v1/users/me/meals/{id}/delete` — meal delete
- `GET /api/v1/users/me/meals/history` — meal history (paginated)
- `GET /api/v1/users/me/meals/changes` — meal sync (paginated)
- `POST /api/v1/logs/error` — client error forwarding

**v2 (Foundation Sprint)**:

- `POST /api/v2/telemetry/events/batch` — telemetry ingest (requires `TELEMETRY_ENABLED=true`)
- `GET /api/v2/users/me/state?day=YYYY-MM-DD` — nutrition state
- `GET /api/v2/users/me/habits` — habit signals
- `POST /api/v2/ai/chat/runs` — canonical AI Chat v2 run lifecycle

**v2 follow-up technical surface**:

- `GET /api/v2/users/me/coach?day=YYYY-MM-DD` — Coach Insights technical surface built on top of nutrition state + habit signals. No separate coach feature flag is planned.
- `GET /api/v2/users/me/reminders/decision?day=YYYY-MM-DD` — Smart Reminders v1 decision surface. It returns backend reminder decision semantics (`send`, `suppress`, `noop`) for a local day. This is a decision API, not reminder delivery orchestration.
- `GET/POST /api/v1/users/me/notifications/preferences` — active notification settings surface used by mobile canonical flow.

Compatibility-only legacy notification endpoints remain under `/api/v1/users/me/notifications*` (`list/upsert/delete/reconcile-plan`) for older clients. They are deprecated and not part of the canonical Smart Reminders production path.

**Narrow telemetry allowlists**:

- Coach Insights telemetry allowlist is documented in [Coach Insights v1 Semantics](./docs/coach-insights-v1.md).
- Smart Reminders telemetry allowlist is documented in [Smart Reminders v1 Semantics](./docs/smart-reminders-v1.md).

Telemetry props must stay categorical and bounded. Do not send copy, raw reason text, user-authored content, or sensitive profile data.

## Feature flags

| Flag                      | Default | What it controls                                                                                                                       |
| ------------------------- | ------- | -------------------------------------------------------------------------------------------------------------------------------------- |
| `TELEMETRY_ENABLED`       | `false` | Accept v2 batch telemetry events. Also requires mobile `EXPO_PUBLIC_ENABLE_TELEMETRY=true`.                                            |
| `STATE_ENABLED`           | `true`  | Legacy compatibility flag. Launch runtime serves v2 nutrition state by default.                                                       |
| `HABITS_ENABLED`          | `true`  | Legacy compatibility flag. Launch runtime computes habit signals by default.                                                           |
| `SMART_REMINDERS_ENABLED` | `true`  | Legacy compatibility flag. Launch runtime serves v2 Smart Reminders decision endpoint by default.                                     |
| `WEEKLY_REPORTS_ENABLED`  | `true`  | Legacy compatibility flag. Launch runtime serves v2 weekly reports endpoint by default.                                                |
| `AI_GATEWAY_ENABLED`      | `true`  | Enforce AI gateway rules (off-topic rejection). Set to `false` to bypass.                                                              |
| `AI_GATEWAY_ML_ENABLED`   | `false` | ML classifier for gateway. Do not enable without a trained model.                                                                      |

## Backend setup

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Health check: `GET http://127.0.0.1:8000/api/v1/health`

Every HTTP response includes `X-Request-ID`. Use it to correlate client failures, backend logs, and Sentry events.

## Operator docs

- Launch Runbook (mobile repo): `../fitaly/docs/launch-runbook.md` — Go/No-Go, rollback matrix, kill-switch strategy
- [Firestore Backup and Restore Runbook](./docs/firestore-backup-restore.md) — backup cadence, export/import commands, and restore drill checklist
- [Ops Monitoring Runbook](./docs/ops-monitoring-runbook.md) — health/latency thresholds, alerting and incident triage
- [Compliance Ops Runbook](./docs/compliance-ops-runbook.md) — data export/delete flow, retention cadence and privacy incident handling
- [Coach Insights v1 Semantics](./docs/coach-insights-v1.md) — response contract, failure handling, telemetry allowlist
- [Coach Insights v1 Rollout](./docs/coach-insights-v1-rollout.md) — rollout preconditions, verification, rollback behavior
- [Smart Reminders v1 Semantics](./docs/smart-reminders-v1.md) — decision contract, suppression semantics, telemetry allowlist
- [Smart Reminders v1 Rollout](./docs/smart-reminders-v1-rollout.md) — rollout preconditions, verification, rollback path
- [Notifications Legacy Sunset Note](./docs/notifications-legacy-sunset-note.md) — compatibility-only residue status and removal criteria

## Required Environment Variables

Use [.env.example](./.env.example) as the source of truth for local and deployment configuration. In `ENVIRONMENT=production`, startup now fails fast when critical integrations are missing or misconfigured (`CORS_ORIGINS`, `OPENAI_API_KEY`, `FIREBASE_PROJECT_ID`, Firebase credentials).

| Variable                         | Required                      | Default                   | Purpose                                                        |
| -------------------------------- | ----------------------------- | ------------------------- | -------------------------------------------------------------- |
| `APP_NAME`                       | No                            | `Fitaly Food Scanner API` | API title in docs/metadata                                     |
| `VERSION`                        | No                            | `0.1.0`                   | API version exposed by app                                     |
| `DEBUG`                          | No                            | `false`                   | FastAPI debug mode                                             |
| `ENVIRONMENT`                    | No                            | `local`                   | `local`, `development`, `staging`, `production`                |
| `OPENAI_API_KEY`                 | Yes in production             | -                         | Auth for OpenAI API calls                                      |
| `CORS_ORIGINS`                   | Yes in production             | -                         | Comma-separated frontend origins (`*` forbidden in production) |
| `FIREBASE_PROJECT_ID`            | Yes in production             | -                         | Firebase project selection                                     |
| `GOOGLE_APPLICATION_CREDENTIALS` | One of required in production | -                         | Path to Firebase service account JSON                          |
| `FIREBASE_CLIENT_EMAIL`          | One of required in production | -                         | Service account email; preferred on Railway                    |
| `FIREBASE_PRIVATE_KEY`           | One of required in production | -                         | Service account private key; preferred on Railway              |
| `SENTRY_DSN`                     | No                            | empty                     | Sentry DSN; empty disables Sentry                              |
| `SENTRY_ENVIRONMENT`             | No                            | `development`             | Sentry environment tag                                         |
| `AI_CREDITS_FREE`                | No                            | `100`                     | Monthly AI credit allocation for free users                    |
| `AI_CREDITS_PREMIUM`             | No                            | `800`                     | Monthly AI credit allocation for premium users                 |
| `AI_CREDIT_COST_CHAT`            | No                            | `1`                       | Credits per chat request                                       |
| `AI_CREDIT_COST_PHOTO`           | No                            | `5`                       | Credits per photo analysis                                     |
| `AI_CREDIT_COST_TEXT_MEAL`       | No                            | `1`                       | Credits per text meal analysis                                 |
| `AI_GATEWAY_ENABLED`             | No                            | `true`                    | Enforce AI gateway rules                                       |
| `TELEMETRY_ENABLED`              | No                            | `false`                   | Accept v2 telemetry batches                                    |
| `HABITS_ENABLED`                 | No                            | `true`                    | Compute habit signals                                          |
| `STATE_ENABLED`                  | No                            | `true`                    | Serve v2 nutrition state                                       |
| `SMART_REMINDERS_ENABLED`        | No                            | `true`                    | Serve v2 Smart Reminders decision endpoint                     |
| `WEEKLY_REPORTS_ENABLED`         | No                            | `true`                    | Serve v2 weekly reports endpoint                               |
| `PORT`                           | Railway only                  | set by Railway            | Runtime HTTP port                                              |

Example local `.env`:

```env
APP_NAME=Fitaly Food Scanner API
DESCRIPTION=Backend API for Fitaly mobile application.
VERSION=0.1.0
DEBUG=true
API_V1_PREFIX=/api/v1
ENVIRONMENT=development
OPENAI_API_KEY=your_openai_key
CORS_ORIGINS=http://localhost:19006
FIREBASE_PROJECT_ID=your_project_id
GOOGLE_APPLICATION_CREDENTIALS=/absolute/path/to/service-account.json
FIREBASE_CLIENT_EMAIL=your_service_account_email
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
SENTRY_DSN=https://xxxx.ingest.sentry.io/xxxx
SENTRY_ENVIRONMENT=development
AI_CREDITS_FREE=100
AI_CREDITS_PREMIUM=800
AI_CREDIT_COST_CHAT=1
AI_CREDIT_COST_TEXT_MEAL=1
AI_CREDIT_COST_PHOTO=5
```

## Railway Deployment

### .env setup

`.env.example` lists the variables expected by the backend for local development and production deployment. For local work, copy it to `.env`, fill in your own values, and keep secrets out of version control.

```bash
cp .env.example .env
```

For local development you can either set `GOOGLE_APPLICATION_CREDENTIALS` to the absolute path of a Firebase service account JSON file or fill in `FIREBASE_CLIENT_EMAIL` and `FIREBASE_PRIVATE_KEY` directly. Then add the remaining values required by the integrations you want to use.

### Deploy On Railway

1. Create a new Railway project and connect it to the repository that contains this backend.
2. If the repository is a monorepo, set the Railway working directory to the backend folder that contains `app/main.py` and this `README.md`.
3. Open the `Variables` tab and add every variable from `.env.example` without surrounding quotes.
4. Pay special attention to these values: `OPENAI_API_KEY`, `FIREBASE_PROJECT_ID`, `FIREBASE_CLIENT_EMAIL`, `FIREBASE_PRIVATE_KEY`, `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `AI_CREDITS_FREE`, `AI_CREDITS_PREMIUM`, `AI_CREDIT_COST_CHAT`, `AI_CREDIT_COST_TEXT_MEAL`, `AI_CREDIT_COST_PHOTO`, `ENVIRONMENT`, `DEBUG`, and `CORS_ORIGINS`.
5. Prefer setting `FIREBASE_CLIENT_EMAIL` and `FIREBASE_PRIVATE_KEY` directly in Railway. Use `GOOGLE_APPLICATION_CREDENTIALS` only as a fallback when your deploy process explicitly creates a service account JSON file at runtime.
6. Set the start command to the Gunicorn command below, or rely on the repository `Procfile`.

```bash
gunicorn -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:$PORT app.main:app
```

7. Do not set `PORT` manually. Railway injects it automatically for the running container.
8. Keep `DEBUG=false` in production and set `ENVIRONMENT=production` plus `SENTRY_ENVIRONMENT=production` for live deployments.
9. Deploy the service and wait for Railway to generate the public domain.
10. Verify the live service on the generated domain:

```text
GET https://<your-domain>/api/v1/health
GET https://<your-domain>/api/v1/version
```

### Sentry & Firestore notes

Create a Sentry project in the Sentry dashboard, copy its DSN from the project settings, and set it as `SENTRY_DSN`. Use `SENTRY_ENVIRONMENT` to distinguish development, staging, and production events.
Sentry initialization is skipped automatically when `ENVIRONMENT=local` and during `pytest` runs, so local tests do not send synthetic failures.

Repository-level release/ops workflows additionally expect these GitHub secrets:

- `OPS_ALERT_DISCORD_WEBHOOK_URL`
- `GCP_WORKLOAD_IDENTITY_PROVIDER`
- `GCP_SERVICE_ACCOUNT_EMAIL`
- `FIRESTORE_SOURCE_PROJECT_ID`
- `FIRESTORE_BACKUP_BUCKET`
- `FIRESTORE_RESTORE_PROJECT_ID`
- `RESTORE_BACKEND_BASE_URL`

For Firebase and Firestore, generate a service account in the Firebase Console or Google Cloud Console. On Railway, the preferred setup is to copy `client_email` into `FIREBASE_CLIENT_EMAIL` and `private_key` into `FIREBASE_PRIVATE_KEY`, keeping them as protected variables. For local development, you can still use the downloaded service account JSON file with `GOOGLE_APPLICATION_CREDENTIALS`. Never commit that JSON file to the repository, and rotate credentials if they are ever exposed.

## Firestore (Short)

- `google-cloud-firestore` is used for direct Firestore operations (documents, queries, transactions).
- `firebase-admin` is used for backend Firebase operations (for example auth token verification and admin-level access).
- Recommended in production: use a dedicated service account with minimal permissions.

## AI Endpoints

Before using AI endpoints, ensure `OPENAI_API_KEY` and credits settings (`AI_CREDITS_*`, `AI_CREDIT_COST_*`) are set in `.env` (see `.env.example`).

`GET /api/v1/ai/credits` returns current credits status for the authenticated user.

Example response:

```json
{
  "userId": "abc",
  "tier": "free",
  "balance": 95,
  "allocation": 100,
  "periodStartAt": "2026-03-01T08:00:00Z",
  "periodEndAt": "2026-04-01T08:00:00Z",
  "costs": {
    "chat": 1,
    "textMeal": 1,
    "photo": 5
  }
}
```

`POST /api/v2/ai/chat/runs` is the only backend AI chat entrypoint used by the mobile app.
Legacy v1 ask endpoint has been removed.

`POST /api/v1/ai/photo/analyze` is the backend photo-analysis entrypoint used by the mobile app for meal-photo AI flows.

Error responses:

```json
{
  "detail": {
    "message": "AI credits exhausted",
    "code": "AI_CREDITS_EXHAUSTED"
  }
}
```

```json
{
  "detail": "AI service unavailable"
}
```

## Sentry (Short)

- `sentry-sdk[fastapi]` captures unhandled errors and performance traces from the API.
- Minimum setup: set `SENTRY_DSN` and initialize Sentry at app startup.
- Keep different `SENTRY_ENVIRONMENT` values for `development`, `staging`, and `production`.

## Error Monitoring

Set `SENTRY_DSN` in `.env` to enable Sentry reporting:

```env
SENTRY_DSN=https://xxxx.ingest.sentry.io/xxxx
```

If `SENTRY_DSN` is empty, Sentry stays disabled and the backend only writes logs locally. When configured, the backend reports unhandled exceptions, selected log messages, and request performance traces to Sentry.

All responses include the `X-Request-ID` header. This identifier is attached to request logs and should be included in support/debug flows when correlating backend activity with client reports.

## Client Error Log Endpoint

`POST /api/v1/logs/error` accepts frontend error reports and forwards them to the backend logger. If Sentry is enabled, these events can also be forwarded there through the centralized logging service. The endpoint accepts anonymous reports, but when a valid Bearer token is present the backend derives the user identity from the token and ignores any client-supplied user identifier.

## License

This repository is distributed under a proprietary, all-rights-reserved license. See [LICENSE](./LICENSE).

Request body fields:

- `source` - client/app area that produced the error
- `message` - human-readable error message
- `stack` - optional stack trace string
- `context` - optional JSON object with extra metadata

Payload limits:

- `source` - up to 120 characters
- `message` - up to 2000 characters
- `stack` - up to 20000 characters
- `context` - serialized JSON up to 8000 characters

Example request:

```json
{
  "source": "mobile.scan-screen",
  "message": "Camera permission check failed",
  "stack": "Error: Camera permission check failed\n    at checkPermission (...)",
  "context": {
    "platform": "ios",
    "appVersion": "1.2.0"
  }
}
```

Example response:

```json
{
  "detail": "logged"
}
```
