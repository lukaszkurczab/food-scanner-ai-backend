"""Shared domain exceptions used across API, service, and DB layers."""


class AiCreditsExhaustedError(Exception):
    """Raised when AI credits are exhausted for a requested action."""


class OpenAIServiceError(Exception):
    """Raised when the OpenAI API returns an error or times out."""


class ContentBlockedError(Exception):
    """Raised when a user's message contains blocked content."""


class FirestoreServiceError(Exception):
    """Raised when an error occurs while interacting with Firestore."""
