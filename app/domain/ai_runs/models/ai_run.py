from dataclasses import dataclass, field
from typing import Any, Literal

RunStatus = Literal["started", "completed", "failed", "rejected"]


def _empty_tools_used() -> list[str]:
    return []


def _empty_tool_metrics() -> list[dict[str, Any]]:
    return []


def _empty_metadata() -> dict[str, Any]:
    return {}


@dataclass(slots=True)
class AiRun:
    id: str
    user_id: str
    thread_id: str
    status: RunStatus
    created_at: int
    updated_at: int
    outcome: RunStatus | None = None
    failure_reason: str | None = None
    planner_used: bool = False
    tools_used: list[str] = field(default_factory=_empty_tools_used)
    tool_metrics: list[dict[str, Any]] = field(default_factory=_empty_tool_metrics)
    summary_used: bool = False
    truncated: bool = False
    retry_count: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    total_latency_ms: int = 0
    metadata: dict[str, Any] = field(default_factory=_empty_metadata)
