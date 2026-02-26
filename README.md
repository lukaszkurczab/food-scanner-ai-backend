# CaloriAI Backend (FastAPI)

## Purpose

This backend provides the API layer for the CaloriAI mobile app.
It handles health/status endpoints now and is prepared for AI, Firebase/Firestore, and observability integrations.

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

## Required Environment Variables

Current codebase requires only app config variables. Integration variables below are required once related features are enabled.

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `APP_NAME` | No | `CaloriAI Food Scanner API` | API title in docs/metadata |
| `VERSION` | No | `0.1.0` | API version exposed by app |
| `DEBUG` | No | `false` | FastAPI debug mode |
| `API_V1_PREFIX` | No | `/api/v1` | Global API route prefix |
| `API_V2_PREFIX` | No | `/api/v2` | Next API version route prefix |
| `OPENAI_API_KEY` | Yes (AI features) | - | Auth for OpenAI API calls |
| `FIREBASE_PROJECT_ID` | Yes (Firebase/Firestore features) | - | Firebase project selection |
| `FIREBASE_CLIENT_EMAIL` | Yes (Firebase/Firestore features) | - | Service account client email |
| `FIREBASE_PRIVATE_KEY` | Yes (Firebase/Firestore features) | - | Service account private key |
| `SENTRY_DSN` | Yes (Sentry enabled) | - | Sentry project DSN |
| `SENTRY_ENVIRONMENT` | No | `development` | Sentry environment tag |
| `PORT` | Railway only | set by Railway | Runtime HTTP port |

Example local `.env`:

```env
APP_NAME=CaloriAI Food Scanner API
VERSION=0.1.0
DEBUG=true
API_V1_PREFIX=/api/v1
API_V2_PREFIX=/api/v2
OPENAI_API_KEY=your_openai_key
FIREBASE_PROJECT_ID=your_project_id
FIREBASE_CLIENT_EMAIL=your_service_account_email
FIREBASE_PRIVATE_KEY="-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
SENTRY_DSN=https://xxxx.ingest.sentry.io/xxxx
SENTRY_ENVIRONMENT=development
```

## Railway Deployment

1. Create a new Railway project and connect the backend repo.
2. Set project root to the backend directory (if needed).
3. Add environment variables from the table above.
4. Set start command:

```bash
gunicorn -k uvicorn.workers.UvicornWorker -w 2 -b 0.0.0.0:$PORT app.main:app
```

5. Deploy and verify:

```text
GET https://<your-domain>/api/v1/health
```

Notes:
- Railway injects `PORT` automatically.
- Keep `DEBUG=false` in production.

## Firestore (Short)

- `google-cloud-firestore` is used for direct Firestore operations (documents, queries, transactions).
- `firebase-admin` is used for backend Firebase operations (for example auth token verification and admin-level access).
- Recommended in production: use a dedicated service account with minimal permissions.

## Sentry (Short)

- `sentry-sdk[fastapi]` captures unhandled errors and performance traces from the API.
- Minimum setup: set `SENTRY_DSN` and initialize Sentry at app startup.
- Keep different `SENTRY_ENVIRONMENT` values for `development`, `staging`, and `production`.
