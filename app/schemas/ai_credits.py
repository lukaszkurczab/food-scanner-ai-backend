from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class CreditCosts(BaseModel):
    chat: int = Field(ge=0)
    textMeal: int = Field(ge=0)
    photo: int = Field(ge=0)


class AiCreditsStatus(BaseModel):
    userId: str
    tier: Literal["free", "premium"]
    balance: int = Field(ge=0)
    allocation: int = Field(ge=0)
    periodStartAt: datetime
    periodEndAt: datetime
    costs: CreditCosts
    renewalAnchorSource: str | None = None
    revenueCatEntitlementId: str | None = None
    revenueCatExpirationAt: datetime | None = None
    lastRevenueCatEventId: str | None = None


class AiCreditsResponse(AiCreditsStatus):
    pass


class RevenueCatWebhookPayload(BaseModel):
    model_config = ConfigDict(extra="allow")

    api_version: str | None = None
    event: dict[str, Any]
