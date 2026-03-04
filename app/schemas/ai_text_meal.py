from pydantic import BaseModel, Field

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
    usageCount: float
    remaining: float
    dateKey: str
    version: str
    persistence: AiPersistence
