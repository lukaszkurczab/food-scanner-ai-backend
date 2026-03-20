"""Shared domain exceptions used across API, service, and DB layers."""


class AiCreditsExhaustedError(Exception):
    """Raised when AI credits are exhausted for a requested action."""


class OpenAIServiceError(Exception):
    """Raised when the OpenAI API returns an error or times out."""


class ContentBlockedError(Exception):
    """Raised when a user's message contains blocked content."""


class FirestoreServiceError(Exception):
    """Raised when an error occurs while interacting with Firestore."""


class TelemetryDisabledError(Exception):
    """Raised when telemetry ingestion is disabled by configuration."""


class TelemetryPayloadTooLargeError(Exception):
    """Raised when a telemetry request exceeds configured payload limits."""


class TelemetryRateLimitError(Exception):
    """Raised when a telemetry client exceeds the allowed request rate."""


class HabitsDisabledError(Exception):
    """Raised when habit signal computation is disabled by configuration."""


class StateDisabledError(Exception):
    """Raised when nutrition state computation is disabled by configuration."""


class CoachUnavailableError(Exception):
    """Raised when coach insights cannot be computed from required foundations."""


class AiGatewayRateLimitError(Exception):
    """Raised when a user exceeds the AI gateway request rate limit."""


class AiGatewayPayloadTooLargeError(Exception):
    """Raised when an AI request payload exceeds gateway guardrails."""
