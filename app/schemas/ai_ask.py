from typing import Dict, Optional

from pydantic import BaseModel


class AiAskRequest(BaseModel):
    userId: str
    message: str
    context: Optional[Dict] = None


class AiAskResponse(BaseModel):
    userId: str
    reply: str
    usageCount: int
    remaining: int
    dateKey: str
    version: str
