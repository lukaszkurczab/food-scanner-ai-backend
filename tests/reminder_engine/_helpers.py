import json
from datetime import UTC, datetime
from pathlib import Path

from app.schemas.nutrition_state import NutritionStateResponse
from app.services.reminder_engine.types import ReminderContextInput

FIXTURES_DIR = Path(__file__).parent.parent / "contract_fixtures"


def load_state_fixture() -> NutritionStateResponse:
    payload = json.loads((FIXTURES_DIR / "nutrition_state.json").read_text(encoding="utf-8"))
    return NutritionStateResponse.model_validate(payload)


def context(hour: int, minute: int) -> ReminderContextInput:
    return ReminderContextInput(now_local=datetime(2026, 3, 18, hour, minute, tzinfo=UTC))
