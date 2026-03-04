from pydantic import BaseModel, Field


class UsernameAvailabilityResponse(BaseModel):
    username: str
    available: bool


class UsernameClaimRequest(BaseModel):
    username: str = Field(min_length=1)


class UsernameClaimResponse(BaseModel):
    username: str
    updated: bool
