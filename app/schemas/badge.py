from pydantic import BaseModel, Field


class PremiumBadgeReconcileRequest(BaseModel):
    isPremium: bool
    nowMs: int | None = Field(default=None, ge=0)


class PremiumBadgeReconcileResponse(BaseModel):
    awardedBadgeIds: list[str] = Field(default_factory=list)
    hasPremiumBadge: bool
    updated: bool


class BadgeItemResponse(BaseModel):
    id: str
    type: str
    label: str
    milestone: int | str
    icon: str
    color: str
    unlockedAt: int = Field(ge=0)


def _badge_items_default() -> list[BadgeItemResponse]:
    return []


class BadgeListResponse(BaseModel):
    items: list[BadgeItemResponse] = Field(default_factory=_badge_items_default)
