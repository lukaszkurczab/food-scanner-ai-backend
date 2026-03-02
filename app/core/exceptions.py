"""Shared domain exceptions used across API, service, and DB layers."""


class AiUsageLimitExceededError(Exception):
    """Raised when daily AI usage exceeds the allowed limit."""


class OpenAIServiceError(Exception):
    """Raised when the OpenAI API returns an error or times out."""


class ContentBlockedError(Exception):
    """Raised when a user's message contains blocked content."""


class FirestoreServiceError(Exception):
    """Raised when an error occurs while interacting with Firestore."""
