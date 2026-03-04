from app.services.ai_chat_prompt_service import build_chat_prompt


def test_build_chat_prompt_derives_policy_from_raw_context() -> None:
    prompt = build_chat_prompt(
        "What should I eat tonight?",
        {
            "language": "en",
            "profile": {
                "preferences": ["highProtein", "glutenFree"],
                "allergies": ["gluten"],
                "activityLevel": "moderate",
                "goal": "maintain",
                "sex": "male",
                "age": "31",
                "height": "182",
                "weight": "82",
                "unitsSystem": "metric",
                "calorieTarget": 2200,
                "aiStyle": "friendly",
                "aiFocus": "mealPlanning",
            },
            "meals": [
                {
                    "timestamp": "2026-03-03T10:00:00.000Z",
                    "name": "Pasta",
                }
            ],
            "history": [
                {"from": "user", "text": "I want something light"},
                {"from": "ai", "text": "Try more vegetables"},
            ],
        },
        language="en",
    )

    assert "Reply in en." in prompt
    assert "TONE=F" in prompt
    assert "FOCUS=MP" in prompt
    assert "FLAGS=highProtein,glutenFree" in prompt
    assert "AVOID=pszenica,jeczmien,zyto,makaron pszenny,pieczywo pszenne" in prompt
    assert "PROFILE=g=maintain" in prompt
    assert "MEALS=1|2026-03-03:Pasta" in prompt
    assert "HISTORY=user: I want something light | ai: Try more vegetables" in prompt
    assert "USER_MESSAGE=What should I eat tonight?" in prompt


def test_build_chat_prompt_supports_legacy_context_shape() -> None:
    prompt = build_chat_prompt(
        "Suggest dinner",
        {
            "flags": ["highProtein"],
            "avoid": ["sugar"],
            "tone": "C",
            "focus": "QA",
            "profile": "g=maintain; kcal=2200",
            "mealsSummary": "2|2026-03-03:Pasta",
            "history": ["Question one", "Question two"],
        },
        language="en",
    )

    assert "TONE=C" in prompt
    assert "FOCUS=QA" in prompt
    assert "FLAGS=highProtein" in prompt
    assert "AVOID=sugar" in prompt
    assert "PROFILE=g=maintain; kcal=2200" in prompt
    assert "MEALS=2|2026-03-03:Pasta" in prompt
    assert "HISTORY=Question one | Question two" in prompt
