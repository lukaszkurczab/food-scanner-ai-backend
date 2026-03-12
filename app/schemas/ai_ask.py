from datetime import datetime
from typing import Any, Dict, Literal, Optional

from pydantic import BaseModel

from app.schemas.ai_credits import CreditCosts
from app.schemas.ai_common import AiPersistence


class AiAskRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None


class AiAskResponse(BaseModel):
    reply: str
    balance: int
    allocation: int
    tier: Literal["free", "premium"]
    periodStartAt: datetime
    periodEndAt: datetime
    costs: CreditCosts
    version: str
    persistence: AiPersistence
