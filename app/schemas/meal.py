from typing import Literal

from pydantic import BaseModel, Field


MealType = Literal["breakfast", "lunch", "dinner", "snack", "other"]
MealSource = Literal["ai", "manual", "saved"] | None
MealSyncState = Literal["synced", "pending", "conflict", "failed"]
MealInputMethod = Literal["manual", "photo", "barcode", "text", "saved", "quick_add"]


class MealIngredient(BaseModel):
    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    amount: float
    unit: Literal["g", "ml"] | None = None
    kcal: float = 0
    protein: float = 0
    fat: float = 0
    carbs: float = 0


class MealTotals(BaseModel):
    protein: float = 0
    fat: float = 0
    carbs: float = 0
    kcal: float = 0


class MealAiMeta(BaseModel):
    model: str | None = None
    runId: str | None = None
    confidence: float | None = None
    warnings: list[str] = Field(default_factory=list)


def _meal_ingredients_default() -> list[MealIngredient]:
    return []


class MealItem(BaseModel):
    userUid: str
    mealId: str
    timestamp: str
    dayKey: str | None = None
    loggedAtLocalMin: int | None = Field(default=None, ge=0, le=1439)
    tzOffsetMin: int | None = Field(default=None, ge=-840, le=840)
    type: MealType
    name: str | None = None
    ingredients: list[MealIngredient] = Field(default_factory=_meal_ingredients_default)
    createdAt: str
    updatedAt: str
    syncState: MealSyncState = "synced"
    source: MealSource = None
    inputMethod: MealInputMethod | None = None
    aiMeta: MealAiMeta | None = None
    imageId: str | None = None
    photoUrl: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    deleted: bool = False
    cloudId: str
    totals: MealTotals = Field(default_factory=MealTotals)


class MealsHistoryPageResponse(BaseModel):
    items: list[MealItem]
    nextCursor: str | None = None


class MealChangesPageResponse(BaseModel):
    items: list[MealItem]
    nextCursor: str | None = None


class MealUpsertRequest(BaseModel):
    mealId: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    dayKey: str | None = None
    loggedAtLocalMin: int | None = Field(default=None, ge=0, le=1439)
    tzOffsetMin: int | None = Field(default=None, ge=-840, le=840)
    type: MealType
    name: str | None = None
    ingredients: list[MealIngredient] = Field(default_factory=_meal_ingredients_default)
    createdAt: str | None = None
    updatedAt: str | None = None
    syncState: MealSyncState | None = None
    source: MealSource = None
    inputMethod: MealInputMethod | None = None
    aiMeta: MealAiMeta | None = None
    imageId: str | None = None
    photoUrl: str | None = None
    notes: str | None = None
    tags: list[str] = Field(default_factory=list)
    deleted: bool = False
    cloudId: str | None = None
    totals: MealTotals | None = None
    userUid: str | None = None


class MealUpsertResponse(BaseModel):
    meal: MealItem
    updated: bool


class MealDeleteRequest(BaseModel):
    updatedAt: str = Field(min_length=1)


class MealDeleteResponse(BaseModel):
    mealId: str
    updatedAt: str
    deleted: bool


class MealPhotoUploadResponse(BaseModel):
    mealId: str | None = None
    imageId: str
    photoUrl: str
