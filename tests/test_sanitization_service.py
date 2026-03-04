from app.services.sanitization_service import sanitize_context, sanitize_request


def test_sanitize_request_masks_emails_and_coarsens_numbers() -> None:
    sanitized = sanitize_request("I am 31 and my email is foo@example.com")

    assert sanitized == "I am 30-40 and my email is [email]"


def test_sanitize_context_sanitizes_profile_history_and_meals() -> None:
    sanitized = sanitize_context(
        {
            "language": "en",
            "actionType": "chat",
            "profile": {
                "age": "31",
                "height": "182",
                "weight": "82",
                "aiNote": "Contact me at foo@example.com, I am 31",
                "goal": "maintain",
            },
            "history": [
                {"from": "user", "text": "My email is foo@example.com and I am 31"},
                "I ate 42 grams of sugar",
            ],
            "meals": [
                {
                    "timestamp": "2026-03-03T10:00:00.000Z",
                    "name": "Lunch with foo@example.com 31",
                }
            ],
        }
    )

    assert sanitized == {
        "language": "en",
        "actionType": "chat",
        "profile": {
            "age": "30-40",
            "height": "180-190",
            "weight": "80-90",
            "aiNote": "Contact me at [email], I am 30-40",
            "goal": "maintain",
        },
        "history": [
            {"from": "user", "text": "My email is [email] and I am 30-40"},
            "I ate 40-50 grams of sugar",
        ],
        "meals": [
            {
                "timestamp": "2026-03-03T10:00:00.000Z",
                "name": "Lunch with [email] 30-40",
            }
        ],
    }
