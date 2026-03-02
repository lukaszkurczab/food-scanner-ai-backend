"""Helpers for blocking disallowed prompt topics.

The blocked keyword list is intentionally small for now and can be expanded
later as moderation rules evolve.
"""

import re

from app.core.exceptions import ContentBlockedError

BLOCKED_KEYWORDS = [
    "medycyna",
    "choroba",
    "lek",
    "symptom",
    "medicine",
    "disease",
    "therapy",
]


def check_allowed(message: str) -> None:
    """Raise when the message contains blocked medical keywords."""
    normalized_message = message.lower()
    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{re.escape(keyword)}\b", normalized_message):
            raise ContentBlockedError("Query contains medical terms not allowed")

    return None
