from pydantic import BaseModel


class AiUsageResponse(BaseModel):
    userId: str
    dateKey: str
    usageCount: int
    dailyLimit: int
    remaining: int
