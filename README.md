# Fitaly Backend (FastAPI)

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
- Add new `v2` endpoints in `app/api/v2/routes/*` and register them in `app/api/v2/router.py` without modifying `v1` handlers.

## Local Run

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Health check:

```text
GET http://127.0.0.1:8000/api/v1/health
```

Run tests:

```bash
pytest -q
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
- `POST /api/v1/ai/ask` — AI chat (gateway-enforced)
- `POST /api/v1/ai/photo/analyze` — photo meal analysis
- `POST /api/v1/ai/text-meal/analyze` — text meal analysis
- `GET /api/v1/ai/credits` — credit balance
- `POST /api/v1/users/me/meals` — meal upsert
- `POST /api/v1/users/me/meals/{id}/delete` — meal delete
- `GET /api/v1/users/me/meals/history` — meal history (paginated)
- `GET /api/v1/users/me/meals/changes` — meal sync (paginated)
- `POST /api/v1/logs/error` — client error forwarding

**v2 (Foundation Sprint)** — behind feature flags:

- `POST /api/v2/telemetry/events/batch` — telemetry ingest (requires `TELEMETRY_ENABLED=true`)
- `GET /api/v2/users/me/state?day=YYYY-MM-DD` — nutrition state (requires `STATE_ENABLED=true`)
- `GET /api/v2/users/me/habits` — habit signals (requires `HABITS_ENABLED=true`)

**v2 follow-up technical surface**:

- `GET /api/v2/users/me/coach?day=YYYY-MM-DD` — Coach Insights technical surface built on top of nutrition state + habit signals. No separate coach feature flag is planned.

**Coach Insights telemetry allowlist**:

- `coach_card_viewed` — `insightType`, `actionType`, `isPositive`
- `coach_card_expanded` — `insightType`
- `coach_card_cta_clicked` — `insightType`, `actionType`, `targetScreen`
- `coach_empty_state_viewed` — `emptyReason`

Coach insight telemetry stays intentionally narrow. Do not send card `title`, `body`, reason text, or any user-authored content in telemetry props.

## Feature flags

| Flag | Default | What it controls |
|------|---------|-----------------|
| `TELEMETRY_ENABLED` | `false` | Accept v2 batch telemetry events. Also requires mobile `EXPO_PUBLIC_ENABLE_TELEMETRY=true`. |
| `STATE_ENABLED` | `false` | Serve v2 nutrition state. Also requires mobile `EXPO_PUBLIC_ENABLE_V2_STATE=true`. |
| `HABITS_ENABLED` | `false` | Compute habit signals (consumed inside state endpoint and standalone). |
| `AI_GATEWAY_ENABLED` | `true` | Enforce AI gateway rules (off-topic rejection). Set to `false` to bypass. |
| `AI_GATEWAY_ML_ENABLED` | `false` | ML classifier for gateway. Do not enable without a trained model. |

To enable a foundation surface for QA/internal, set the backend flag in `.env` or Railway, restart, then enable the paired mobile flag and rebuild the app.  See [Foundation Rollout Runbook](../docs/runbooks/foundation-rollout-runbook.md) for step-by-step.

## Backend setup

```bash
pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Health check: `GET http://127.0.0.1:8000/api/v1/health`

Every HTTP response includes `X-Request-ID`.  Use it to correlate client failures, backend logs, and Sentry events.

## Operator docs

- [Foundation Contracts](../docs/contracts/foundation-contracts.md) — canonical shapes for all cross-repo contracts
- [Foundation Rollout Runbook](../docs/runbooks/foundation-rollout-runbook.md) — enable/disable/rollback steps
- [Foundation Observability](../docs/monitoring/foundation-observability.md) — what to monitor, suggested alerts
- [Foundation Hardening Plan](../docs/foundation/foundation-hardening-plan.md) — completed PRs, remaining gaps, exit criteria
- [Telemetry Taxonomy](../docs/telemetry-taxonomy.md) — event names, property rules, payload limits
- [Foundation QA Checklist](../docs/foundation-qa-checklist.md) — automated + manual QA coverage
- [Foundation V2 Rollout Playbook](../docs/foundation-v2-rollout-playbook.md) — original rollout strategy

## Required Environment Variables

Use [.env.example](./.env.example) as the source of truth for local and deployment configuration. Core app variables are optional because the backend has defaults, but integration variables become required as soon as Firebase, Firestore, OpenAI, or Sentry are enabled.

| Variable | Required | Default | Purpose |
|----------|----------|---------|---------|
| `APP_NAME` | No | `Fitaly Food Scanner API` | API title in docs/metadata |
| `VERSION` | No | `0.1.0` | API version exposed by app |
| `DEBUG` | No | `false` | FastAPI debug mode |
| `ENVIRONMENT` | No | `local` | `local`, `development`, `staging`, `production` |
| `OPENAI_API_KEY` | Yes (AI features) | - | Auth for OpenAI API calls |
| `CORS_ORIGINS` | No | `*` fallback | Comma-separated frontend origins |
| `FIREBASE_PROJECT_ID` | Yes (Firestore) | - | Firebase project selection |
| `GOOGLE_APPLICATION_CREDENTIALS` | Optional | - | Path to Firebase service account JSON |
| `FIREBASE_CLIENT_EMAIL` | Yes (Firestore) | - | Service account email; preferred on Railway |
| `FIREBASE_PRIVATE_KEY` | Yes (Firestore) | - | Service account private key; preferred on Railway |
| `SENTRY_DSN` | No | empty | Sentry DSN; empty disables Sentry |
| `SENTRY_ENVIRONMENT` | No | `development` | Sentry environment tag |
| `AI_CREDITS_FREE` | No | `100` | Monthly AI credit allocation for free users |
| `AI_CREDITS_PREMIUM` | No | `800` | Monthly AI credit allocation for premium users |
| `AI_CREDIT_COST_CHAT` | No | `1` | Credits per chat request |
| `AI_CREDIT_COST_PHOTO` | No | `5` | Credits per photo analysis |
| `AI_CREDIT_COST_TEXT_MEAL` | No | `1` | Credits per text meal analysis |
| `AI_GATEWAY_ENABLED` | No | `true` | Enforce AI gateway rules |
| `TELEMETRY_ENABLED` | No | `false` | Accept v2 telemetry batches |
| `HABITS_ENABLED` | No | `false` | Compute habit signals |
| `STATE_ENABLED` | No | `false` | Serve v2 nutrition state |
| `PORT` | Railway only | set by Railway | Runtime HTTP port |

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
AI_DAILY_LIMIT_FREE=20
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
4. Pay special attention to these values: `OPENAI_API_KEY`, `FIREBASE_PROJECT_ID`, `FIREBASE_CLIENT_EMAIL`, `FIREBASE_PRIVATE_KEY`, `SENTRY_DSN`, `SENTRY_ENVIRONMENT`, `AI_DAILY_LIMIT_FREE`, `ENVIRONMENT`, `DEBUG`, and `CORS_ORIGINS`.
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

For Firebase and Firestore, generate a service account in the Firebase Console or Google Cloud Console. On Railway, the preferred setup is to copy `client_email` into `FIREBASE_CLIENT_EMAIL` and `private_key` into `FIREBASE_PRIVATE_KEY`, keeping them as protected variables. For local development, you can still use the downloaded service account JSON file with `GOOGLE_APPLICATION_CREDENTIALS`. Never commit that JSON file to the repository, and rotate credentials if they are ever exposed.

## Firestore (Short)

- `google-cloud-firestore` is used for direct Firestore operations (documents, queries, transactions).
- `firebase-admin` is used for backend Firebase operations (for example auth token verification and admin-level access).
- Recommended in production: use a dedicated service account with minimal permissions.

## AI Endpoints

Before using AI endpoints, ensure `OPENAI_API_KEY` and `AI_DAILY_LIMIT_FREE` are set in `.env` (see `.env.example`).

`GET /api/v1/ai/usage?userId=<id>` returns current daily AI usage.

Example request:

```http
GET /api/v1/ai/usage?userId=abc
```

Example response:

```json
{
  "userId": "abc",
  "dateKey": "2026-03-01",
  "usageCount": 3,
  "dailyLimit": 20,
  "remaining": 17
}
```

`POST /api/v1/ai/ask` is the single backend AI text entrypoint used by the mobile app. It accepts chat-style requests, checks content policy, sanitizes the prompt, increments usage, and forwards the request to OpenAI. User identity is derived from the Bearer token and chat persistence is backend-owned.

Example request:

```json
{
  "message": "Suggest a simple dinner",
  "context": {
    "actionType": "chat",
    "weightKg": 78,
    "goal": "fat loss"
  }
}
```

Example response:

```json
{
  "userId": "abc",
  "reply": "Try grilled chicken, rice, and a side salad.",
  "usageCount": 4,
  "remaining": 16,
  "dateKey": "2026-03-01",
  "version": "0.1.0",
  "persistence": "backend_owned"
}
```

`POST /api/v1/ai/photo/analyze` is the backend photo-analysis entrypoint used by the mobile app for meal-photo AI flows.

Error responses:

```json
{
  "detail": "AI usage limit exceeded"
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
