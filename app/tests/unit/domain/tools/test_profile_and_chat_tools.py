import pytest

from app.core.errors import ConsentRequiredError
from app.domain.chat_memory.models.memory_summary import MemorySummary
from app.domain.tools.get_app_help_context import GetAppHelpContextTool
from app.domain.tools.get_goal_context import GetGoalContextTool
from app.domain.tools.get_profile_summary import GetProfileSummaryTool
from app.domain.tools.get_recent_chat_summary import GetRecentChatSummaryTool
from app.domain.users.models.user_profile import UserProfile
from app.domain.users.services.consent_service import ConsentService
from app.domain.users.services.user_profile_service import UserProfileService


class _FakeUserProfileService:
    def __init__(self, profile_summary: dict, goal_context: dict) -> None:
        self._profile_summary = profile_summary
        self._goal_context = goal_context

    async def get_profile_summary(self, *, user_id: str) -> dict:
        del user_id
        return self._profile_summary

    async def get_goal_context(self, *, user_id: str) -> dict:
        del user_id
        return self._goal_context


class _FakeSummaryService:
    def __init__(self, summary: MemorySummary | None) -> None:
        self.summary = summary

    async def get_current_summary(self, *, user_id: str, thread_id: str) -> MemorySummary | None:
        del user_id, thread_id
        return self.summary


class _FakeMessageService:
    def __init__(self, turns: list[dict[str, str]]) -> None:
        self.turns = turns
        self.called = False

    async def get_recent_turns(self, *, user_id: str, thread_id: str, limit: int) -> list[dict[str, str]]:
        del user_id, thread_id, limit
        self.called = True
        return self.turns


class _FakeConsentProfileService:
    def __init__(self, profile: UserProfile | None) -> None:
        self._profile = profile

    async def get_profile(self, *, user_id: str) -> UserProfile | None:
        del user_id
        return self._profile


async def test_profile_and_goal_tools_return_structured_payloads() -> None:
    profile_tool = GetProfileSummaryTool(
        _FakeUserProfileService(
            profile_summary={
                "goal": "maintain",
                "activityLevel": "moderate",
                "preferences": ["high_protein"],
                "allergies": ["nuts"],
                "language": "pl",
            },
            goal_context={},
        )  # type: ignore[arg-type]
    )
    goal_tool = GetGoalContextTool(
        _FakeUserProfileService(
            profile_summary={},
            goal_context={
                "goal": "maintain",
                "calorieTarget": 2200,
                "proteinStrategy": "balanced_protein_intake",
            },
        )  # type: ignore[arg-type]
    )

    profile = await profile_tool.execute(user_id="user-1", args={})
    goal = await goal_tool.execute(user_id="user-1", args={})

    assert profile["activityLevel"] == "moderate"
    assert profile["preferences"] == ["high_protein"]
    assert goal["calorieTarget"] == 2200


async def test_get_recent_chat_summary_prefers_summary_then_falls_back_to_turns() -> None:
    summary = MemorySummary(
        user_id="user-1",
        thread_id="thread-1",
        summary="Uzytkownik chce podsumowanie bialka.",
        resolved_facts=["fakt-a"],
        covered_until_message_id="msg-1",
        version=1,
        summary_model="gpt-4o-mini",
        created_at=1,
        updated_at=2,
    )

    fallback_turns = [{"role": "user", "content": "hej"}]

    summary_service = _FakeSummaryService(summary=summary)
    message_service = _FakeMessageService(turns=fallback_turns)
    tool = GetRecentChatSummaryTool(summary_service, message_service)  # type: ignore[arg-type]
    result = await tool.execute(
        user_id="user-1",
        args={"threadId": "thread-1"},
    )
    assert result["hasSummary"] is True
    assert result["source"] == "memory_summary"
    assert message_service.called is False

    fallback_tool = GetRecentChatSummaryTool(
        _FakeSummaryService(summary=None),
        message_service,
    )  # type: ignore[arg-type]
    fallback = await fallback_tool.execute(
        user_id="user-1",
        args={"threadId": "thread-1", "fallbackTurnsLimit": 4},
    )
    assert fallback["hasSummary"] is False
    assert fallback["source"] == "recent_turns_fallback"
    assert fallback["lastTurns"] == fallback_turns


async def test_get_app_help_context_returns_deterministic_facts() -> None:
    tool = GetAppHelpContextTool()
    result = await tool.execute(user_id="user-1", args={"topic": "meal_logging"})
    assert result["topic"] == "meal_logging"
    assert len(result["answerFacts"]) >= 2
    assert any("Meals" in item or "podsumowania" in item for item in result["answerFacts"])


async def test_get_app_help_context_normalizes_chat_topic() -> None:
    tool = GetAppHelpContextTool()
    result = await tool.execute(user_id="user-1", args={"topic": "chat_v2"})
    assert result["topic"] == "chat"
    assert any("/api/v2/ai/chat/runs" in item for item in result["answerFacts"])


async def test_consent_service_enforces_ai_health_data_consent() -> None:
    service_without_consent = ConsentService(
        _FakeConsentProfileService(
            UserProfile(
                user_id="user-1",
                ai_health_data_consent_at=None,
                survey_completed=False,
            )
        )  # type: ignore[arg-type]
    )
    assert await service_without_consent.has_ai_health_data_consent(user_id="user-1") is False
    with pytest.raises(ConsentRequiredError):
        await service_without_consent.ensure_ai_health_data_consent(user_id="user-1")

    service_with_consent = ConsentService(
        _FakeConsentProfileService(
            UserProfile(
                user_id="user-1",
                ai_health_data_consent_at="2026-04-19T10:00:00Z",
                survey_completed=False,
            )
        )  # type: ignore[arg-type]
    )
    await service_with_consent.ensure_ai_health_data_consent(user_id="user-1")


async def test_user_profile_service_reuses_user_account_profile_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_get_user_profile_data(user_id: str) -> dict:
        assert user_id == "user-1"
        return {
            "goal": "gain",
            "activityLevel": "high",
            "calorieTarget": 2800,
            "preferences": ["vegan"],
            "allergies": ["soy"],
            "language": "en-US",
            "aiHealthDataConsentAt": "2026-04-10T11:00:00Z",
            "surveyComplited": True,
        }

    monkeypatch.setattr(
        "app.domain.users.services.user_profile_service.user_account_service.get_user_profile_data",
        _fake_get_user_profile_data,
    )

    service = UserProfileService()
    profile = await service.get_profile(user_id="user-1")
    assert profile is not None
    assert profile.language == "en"
    assert profile.calorie_target == 2800
    assert profile.ai_health_data_consent_at == "2026-04-10T11:00:00Z"

    goal_context = await service.get_goal_context(user_id="user-1")
    assert goal_context["proteinStrategy"] == "higher_protein_with_calorie_surplus"
