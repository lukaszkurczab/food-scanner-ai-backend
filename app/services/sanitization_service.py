"""Helpers for masking sensitive user input before sending prompts to AI."""


def sanitize_request(message: str, context: dict | None = None) -> str:
    """Sanitize user input by masking sensitive personal data and ranges.

    Replace specific numbers like weight/age with ranges (e.g. 23 -> "20-30").
    Strip out names or emails if present. Extend rules later if needed.

    :param message: User's raw message.
    :param context: Optional context dictionary (unused for now).
    :return: Sanitized message to send to the OpenAI API.
    """
    import re

    # Replace numbers >=10 and <=120 with ranges for age/weight.
    # Adjust heuristics as needed.
    def replace_number(match):
        try:
            num = int(match.group(0))
        except ValueError:
            return match.group(0)
        if 10 <= num <= 120:
            lower = (num // 10) * 10
            upper = lower + 10
            return f"{lower}-{upper}"
        return match.group(0)

    sanitized = re.sub(r"\b\d+\b", replace_number, message)
    sanitized = re.sub(r"[\w.+-]+@[\w-]+\.[\w.-]+", "[email]", sanitized)

    # Future rules:
    # - mask phone numbers and postal addresses
    # - strip person names from common self-introduction patterns
    # - coarsen dates into broader windows
    # - sanitize selected context fields when context starts being used
    _ = context
    return sanitized
