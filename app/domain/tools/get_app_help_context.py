from app.domain.tools.base import DomainTool
from app.schemas.ai_chat.tools import AppHelpContextDto


class GetAppHelpContextTool(DomainTool):
    name = "get_app_help_context"

    def __init__(self) -> None:
        self._knowledge_base: dict[str, list[str]] = {
            "meal_logging": [
                "Posilek dodajesz w ekranie Meals przez przycisk dodawania wpisu.",
                "Mozesz dodac posilek recznie lub przez analize zdjecia, jesli funkcja jest dostepna.",
                "Edycja/usuniecie wpisu aktualizuje podsumowania dnia i tygodnia w chat v2.",
            ],
            "calorie_target": [
                "Calorie target ustawiasz w profilu uzytkownika.",
                "Zmiana calorieTarget zmienia interpretacje postepu wzgledem celu.",
            ],
            "profile": [
                "Profil przechowuje cel, poziom aktywnosci i preferencje, ktore chat v2 uwzglednia w analizie.",
                "Jezyk aplikacji i prosba uzytkownika steruja jezykiem odpowiedzi.",
            ],
            "chat": [
                "AI Chat v2 dziala backendowo: planner wybiera capabilities, tools pobieraja dane, generator tworzy odpowiedz.",
                "Canonical endpoint chat v2 to POST /api/v2/ai/chat/runs.",
                "Odpowiedz zawiera contextStats (np. toolsUsed, usedSummary, truncated), co pomaga diagnozowac jakosc kontekstu.",
                "Przy niskim coverage logowania chat podaje ostrozniejsza ocene zamiast udawac pelna pewnosc.",
            ],
            "ai_chat": [
                "Canonical endpoint chat v2 to POST /api/v2/ai/chat/runs.",
                "Idempotency jest oparte o clientMessageId w obrebie threadu.",
                "Run telemetry zapisuje m.in. plannerUsed, toolsUsed, retryCount oraz usage tokenow.",
            ],
            "default": [
                "AI Chat v2 odpowiada na podstawie danych backendowych, nie na podstawie zgadywania.",
                "Najpierw planowane sa capabilities, potem liczone summary/views, a dopiero na koncu generowana odpowiedz.",
                "Przy niskim pokryciu logowania odpowiedz powinna jasno sygnalizowac ograniczona pewnosc.",
            ],
        }

    async def execute(self, *, user_id: str, args: dict) -> dict:
        del user_id
        topic = self._normalize_topic(str(args.get("topic") or "default").strip().lower())
        facts = self._knowledge_base.get(topic, self._knowledge_base["default"])
        dto = AppHelpContextDto.model_validate({"topic": topic, "answerFacts": facts})
        return dto.model_dump(by_alias=True)

    @staticmethod
    def _normalize_topic(topic: str) -> str:
        normalized = topic.strip().lower()
        if not normalized:
            return "default"
        if normalized in {"chat", "ai", "ai_chat", "chat_v2", "chatv2"}:
            return "chat"
        return normalized
