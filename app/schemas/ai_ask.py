from typing import Any, Dict, Optional

from pydantic import BaseModel
from app.schemas.ai_common import AiPersistence


class AiAskRequest(BaseModel):
    message: str
    context: Optional[Dict[str, Any]] = None


class AiAskResponse(BaseModel):
    reply: str
    usageCount: float
    remaining: float
    dateKey: str
    version: str
    persistence: AiPersistence
