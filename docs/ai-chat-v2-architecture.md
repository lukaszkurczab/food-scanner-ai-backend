# AI Chat v2 Architecture

## Purpose

This document defines the canonical backend path for AI Chat v2 and the guardrails that prevent legacy chat v1 from returning.

## Canonical v2 Path

- API endpoint:
  - `app/api/v2/endpoints/ai_chat.py`
  - `POST /api/v2/ai/chat/runs`
- DI/wiring:
  - `app/api/v2/deps/ai_chat.py`
- Orchestration:
  - `app/domain/chat/*`
- Deterministic tools:
  - `app/domain/tools/*`
- Thread memory and messages:
  - `app/domain/chat_memory/*`
- AI run telemetry persistence:
  - `app/domain/ai_runs/*`
- Firestore repositories/mappers:
  - `app/infra/firestore/repositories/*`
  - `app/infra/firestore/mappers/*`
- DTO contracts:
  - `app/schemas/ai_chat/*`

## Runtime Lifecycle (v2)

1. auth (endpoint dependency)
2. consent gate
3. idempotency lookup by `clientMessageId` in thread scope
4. ensure/create thread and user message persistence
5. planner
6. tool execution via canonical `ToolRegistry`
7. grounded context build + token budget enforcement
8. generator + retry policy
9. assistant message persistence
10. memory summary refresh
11. ai_run telemetry update
12. response DTO mapping

## v1/v2 Boundary Rules

- Canonical chat runtime is only:
  - `POST /api/v2/ai/chat/runs`
  - `app/api/v2/endpoints/ai_chat.py`
  - `app/api/v2/deps/ai_chat.py`
  - `app/domain/chat/*`
- Removed and forbidden for chat:
  - legacy v1 ask endpoint
  - backward-compat alias exports in `app/api/routes/ai.py` (`legacy_*`, `ai_context_service = ...`, etc.)
  - chat-only v1 modules in `app/services/*` and `app/schemas/ai_ask.py`
- v2 path must not depend on legacy AI context/prompt flow.
- Forbidden in canonical v2 path:
  - `app.services.ai_context_service`
  - `app.services.ai_chat_prompt_service`
  - `app.services.conversation_memory_service`
  - `app.services.ai_token_budget_service`
  - `app.services.sanitization_service`
  - `app.services.openai_service` (for chat flow)
- Allowed legacy v1 AI surface:
  - `app/api/routes/ai.py` endpoints for photo/text meal analysis.

## Test Ownership

Markers:

- `ai_v2`:
  - canonical v2 tests under `app/tests/*`
- `legacy_ai`:
  - legacy AI v1 tests in `tests/*` (analysis/gateway compatibility), not chat v1 runtime.

Recommended commands:

- only v2 tests:
  - `pytest -q -m ai_v2 app/tests`
- only legacy AI v1 compatibility tests:
  - `pytest -q -m legacy_ai tests`

## Known Limitations (Current)

- No distributed lock around same-`clientMessageId` concurrent requests; behavior relies on persistence idempotency + replay checks.
- No cross-document transaction spanning full run/thread/message/summary write set.
- Summary refresh is intentionally lightweight and not a full long-context summarization pipeline.
- Planner/tools/generator orchestration is backend-owned, but deeper adaptive policies are deferred to later iterations.
