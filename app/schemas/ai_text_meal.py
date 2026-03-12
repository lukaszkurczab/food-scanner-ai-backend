from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ai_credits import CreditCosts
from app.schemas.ai_common import AiPersistence


class AiTextMealPayload(BaseModel):
    name: str | None = None
    ingredients: str | None = None
    amount_g: int | None = Field(default=None, gt=0)
    notes: str | None = None


class AiTextMealAnalyzeRequest(BaseModel):
    payload: AiTextMealPayload
    lang: str = Field(default="en", min_length=2, max_length=10)


class AiTextMealIngredient(BaseModel):
    name: str
    amount: float
    protein: float
    fat: float
    carbs: float
    kcal: float
    unit: str | None = None


class AiTextMealAnalyzeResponse(BaseModel):
    ingredients: list[AiTextMealIngredient]
    balance: int
    allocation: int
    tier: Literal["free", "premium"]
    periodStartAt: datetime
    periodEndAt: datetime
    costs: CreditCosts
    version: str
    persistence: AiPersistence
