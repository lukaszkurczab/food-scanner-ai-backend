"""Schema representing error logs sent from the client application."""

import json
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, model_validator

MAX_SOURCE_LENGTH = 120
MAX_MESSAGE_LENGTH = 2_000
MAX_STACK_LENGTH = 20_000
MAX_CONTEXT_JSON_LENGTH = 8_000
MAX_CONTEXT_VALUE_STRING_LENGTH = 300
_SAFE_CONTEXT_KEYS = frozenset(
    {
        "action",
        "beforeCreatedAt",
        "code",
        "environment",
        "feature",
        "lang",
        "messageId",
        "networkState",
        "opId",
        "operation",
        "phase",
        "platform",
        "reason",
        "retryable",
        "screen",
        "source",
        "status",
        "statusCode",
        "surface",
        "threadId",
        "uid",
        "userUid",
    }
)
_SENSITIVE_KEY_MARKERS = (
    "authorization",
    "cookie",
    "content",
    "email",
    "message",
    "password",
    "prompt",
    "stack",
    "text",
    "token",
)


def _is_safe_context_value(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _contains_sensitive_marker(key: str) -> bool:
    lowered = key.lower()
    return any(marker in lowered for marker in _SENSITIVE_KEY_MARKERS)


class ErrorLogRequest(BaseModel):
    source: str = Field(min_length=1, max_length=MAX_SOURCE_LENGTH)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    stack: Optional[str] = Field(default=None, max_length=MAX_STACK_LENGTH)
    context: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_context_size(self) -> "ErrorLogRequest":
        if self.context is None:
            return self

        for key, value in self.context.items():
            if key not in _SAFE_CONTEXT_KEYS:
                raise ValueError(f"Context key '{key}' is not allowlisted")
            if _contains_sensitive_marker(key):
                raise ValueError(f"Context key '{key}' is privacy-sensitive")
            if not _is_safe_context_value(value):
                raise ValueError(f"Context key '{key}' has unsupported value type")
            if isinstance(value, str) and len(value) > MAX_CONTEXT_VALUE_STRING_LENGTH:
                raise ValueError(f"Context key '{key}' value is too long")

        serialized = json.dumps(self.context, ensure_ascii=False, default=str)
        if len(serialized) > MAX_CONTEXT_JSON_LENGTH:
            raise ValueError("Context payload is too large")
        return self
