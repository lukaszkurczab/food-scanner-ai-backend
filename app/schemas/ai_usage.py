from pydantic import BaseModel


class AiUsageResponse(BaseModel):
    dateKey: str
    usageCount: float
    dailyLimit: int
    remaining: float
