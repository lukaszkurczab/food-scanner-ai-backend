"""Schema representing error logs sent from the client application."""

import json
from typing import Any, Dict, Optional

from pydantic import BaseModel, Field, model_validator

MAX_SOURCE_LENGTH = 120
MAX_MESSAGE_LENGTH = 2_000
MAX_STACK_LENGTH = 20_000
MAX_CONTEXT_JSON_LENGTH = 8_000


class ErrorLogRequest(BaseModel):
    source: str = Field(min_length=1, max_length=MAX_SOURCE_LENGTH)
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_LENGTH)
    stack: Optional[str] = Field(default=None, max_length=MAX_STACK_LENGTH)
    context: Optional[Dict[str, Any]] = None

    @model_validator(mode="after")
    def validate_context_size(self) -> "ErrorLogRequest":
        if self.context is None:
            return self

        serialized = json.dumps(self.context, ensure_ascii=False, default=str)
        if len(serialized) > MAX_CONTEXT_JSON_LENGTH:
            raise ValueError("Context payload is too large")
        return self
