from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from app.schemas.ai_credits import CreditCosts
from app.schemas.ai_common import AiPersistence


class AiPhotoAnalyzeRequest(BaseModel):
    imageBase64: str = Field(min_length=1)
    lang: str = Field(default="en", min_length=2, max_length=10)


class AiPhotoIngredient(BaseModel):
    name: str
    amount: float
    protein: float
    fat: float
    carbs: float
    kcal: float
    unit: str | None = None


class AiPhotoAnalyzeResponse(BaseModel):
    ingredients: list[AiPhotoIngredient]
    balance: int
    allocation: int
    tier: Literal["free", "premium"]
    periodStartAt: datetime
    periodEndAt: datetime
    costs: CreditCosts
    version: str
    persistence: AiPersistence
