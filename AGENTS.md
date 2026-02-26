## Project Context

- Stack: Python 3.11+, FastAPI, Uvicorn.
- Backend services: Firebase Admin SDK, Firestore, OpenAI SDK, Sentry.
- Hosting: Railway (initial target), local dev via `fastapi dev` / `uvicorn`.
- Tooling: pytest, httpx (for tests), Ruff, mypy optional.
- Config: `.env` + pydantic-settings (or equivalent typed settings layer).

### Architecture Principles

- **Service-oriented modules as default:** new backend functionality goes into clear layers:
  - `app/api/` for HTTP routes
  - `app/services/` for business logic
  - `app/db/` for database access and external persistence clients
  - `app/schemas/` for request/response models
  - `app/core/` for config, logging, monitoring, shared exceptions, middleware
- **HTTP layer stays thin:** route handlers should only validate input, call services, and map results/errors to HTTP responses.
- **Business logic lives in services:** rate limits, AI orchestration, sanitization, and workflow logic must not live in route files.
- **Database access is isolated:** direct Firestore access should be centralized in `app/db/` or dedicated service adapters, not scattered across routes.
- **No circular dependencies:** `api -> services -> db/core`, never the reverse.
- **Stable boundaries:** request/response contracts should be explicit and typed via schemas; services should expose small, predictable interfaces.

### Naming, Imports, and Boundaries

- **Absolute imports preferred:** use package imports like `from app.services.ai_usage_service import ...` instead of fragile relative imports when crossing folders.
- **Import direction:** `api/routes` -> `services` -> `db` / `core`.
- **Never import from API into services:** services must be reusable outside HTTP context.
- **Naming conventions:**
  - Route modules: `health.py`, `ai.py`, `usage.py`
  - Services: `xxx_service.py` (e.g. `openai_service.py`, `ai_usage_service.py`)
  - DB adapters: `xxx_client.py` or `xxx_repo.py`
  - Schemas: grouped by domain, e.g. `ai.py`, `usage.py`, `errors.py`
  - Exceptions: `exceptions.py` or domain-specific exception modules
- **Export policy:** prefer explicit imports; avoid broad wildcard exports.
- **Barrels:** avoid `__init__.py` re-export barrels unless already necessary for package ergonomics.

## Operating Mode

- For any non-trivial task (>= 3 steps, architectural choice, or ambiguous requirements): start with a short plan (bullets).
- If assumptions change or implementation risk increases: stop, re-plan, and explain.
- Prefer the smallest correct change. Avoid broad refactors unless requested.
- Preserve current API contracts unless explicitly asked to change them.

## Refactor Loop (required)

Applies to any non-trivial task (>= 3 steps), architectural change, refactor, service extraction, boundary cleanup, or dependency-graph change.

### Refactor trigger keywords

A task is considered a refactor if it includes any of:

- "refactor", "architecture", "extract", "move file", "decouple", "service layer", "db layer", "shared logic", "boundary"

### Preflight (default)

Before editing code, run:

1. `mcp__ollama_backend_sidecar__propose_backend_approaches`
   - Input: task description + constraints from this file.
   - Output: 2–3 approaches + recommended approach.

2. `mcp__ollama_backend_sidecar__backend_risk_check`
   - Input: chosen approach + known hotspots.
   - Output: risks + regression vectors + test checklist + rollback plan.

Skip preflight only if the user explicitly says **"skip preflight"**.

### Hard requirement (tool-before-edit)

For refactors/boundary work: **do not edit any files** until both MCP tools have been called:

- `mcp__ollama_backend_sidecar__propose_backend_approaches`
- `mcp__ollama_backend_sidecar__backend_risk_check`

If MCP tools are unavailable or time out: **stop and report**; do not proceed with edits.

### Execution

- Implement **PR1 only** (smallest correct change). Do not start PR2 unless the user says **"continue"**.
- Keep diffs small and behavior unchanged unless explicitly requested.

### Quality Gate (must pass)

Always run and report results:

- `pytest`
- `python -m compileall app`
- `ruff check .`

If mypy is configured, also run:

- `mypy app`

If a gate fails: fix and re-run (max 3 attempts). If still failing: stop and report top blockers with file/line and minimal next step.

## Codebase First

- Search the repo for existing patterns (routing, service layout, config, exception handling, logging) and follow them.
- Do not introduce new dependencies unless explicitly requested.
- Reuse existing helpers for config, error handling, and Firestore access before adding new abstractions.

## Python / FastAPI Rules

- Prefer typed function signatures and Pydantic models for request/response payloads.
- Avoid untyped dictionaries for public interfaces when a schema/model is appropriate.
- Keep route handlers small and async-aware where needed.
- Use dependency injection only where it improves clarity; do not over-engineer.
- Avoid hidden side effects in module import time, except intentional singleton setup (e.g. settings, logger bootstrap).

## Firestore Rules

- Never log or persist secrets or raw credentials.
- Use predictable document shapes and stable document IDs where possible.
- Prefer server-side enforcement of business rules like usage limits.
- Use Firestore transactions for counters / daily limits / concurrency-sensitive writes.
- Minimize read/write count; avoid wasteful read-before-write patterns unless required by transaction logic.
- Handle network and permission failures explicitly.

## OpenAI Rules

- Never expose API keys in code or logs.
- Keep AI orchestration in a dedicated service (`openai_service.py` or equivalent).
- Sanitize and minimize data sent to OpenAI.
- Apply rate-limit / quota checks before external AI calls whenever possible.
- Use timeouts and controlled exception mapping.

## Sentry / Logging Rules

- Sentry must be optional: backend must still run if `SENTRY_DSN` is absent.
- Centralize logging and exception capture in `app/core/`.
- Route files and services should use the shared logger/error helper, not ad-hoc `print()`.
- Do not send sensitive payloads or secrets to logs/Sentry.

## Config / Secrets Rules

- All configuration must come from environment variables or typed settings.
- Do not hardcode API keys, DSNs, project IDs, or credential paths.
- Keep `.env.example` updated when adding new settings.
- Secrets must never be committed or echoed in logs.

## Railway / Deployment Rules

- Keep deployment simple and compatible with Railway.
- Assume startup command is based on Uvicorn/FastAPI.
- Do not require Docker unless explicitly requested.
- Changes must preserve local dev flow and production deploy flow.
- Healthcheck endpoints should remain lightweight and not depend on external APIs.

## Verification Before Done

- Never claim “done” without verification.
- Always run:
  - `pytest`
  - `python -m compileall app`
  - `ruff check .`
- If tests exist for the touched area: run them.
- If no tests exist, provide a short manual verification checklist.
- For API changes: list exact endpoints verified and expected status codes.

## Bug-Fix Protocol

- Given a stack trace/log: identify root cause and fix it, not just symptoms.
- If multiple plausible causes exist: state top 1–2 hypotheses and quickest confirmation path.
- Add a regression test when feasible; otherwise provide a reproducible verification path.
- Prefer fixing at the correct layer (service/db/config), not masking in the route layer.

## API / Firestore Review Tools

Use the dedicated backend MCP tools when relevant:

- For API request/response changes:
  - `mcp__ollama_backend_sidecar__review_api_contract`
- For Firestore write/read design:
  - `mcp__ollama_backend_sidecar__firestore_write_review`
- For endpoint-focused backend test planning:
  - `mcp__ollama_backend_sidecar__endpoint_test_plan`
- For deploy / startup / env impact:
  - `mcp__ollama_backend_sidecar__deployment_review`
- For backend layer ownership / extraction:
  - `mcp__ollama_backend_sidecar__layer_boundary_check`

Use them when the task clearly benefits from a focused review, not only for refactors.

## Communication (IDE)

For each change-set, include:

- What changed (1–3 bullets)
- Why
- How verified (commands run / endpoints checked)

## Guardrails

- No destructive data operations (deleting collections, migrations, schema rewrites, mass overwrites) without explicit confirmation.
- No secrets in code or logs.
- No breaking API contract changes without explicit confirmation.
- No silent fallback that hides production failures unless explicitly requested.
