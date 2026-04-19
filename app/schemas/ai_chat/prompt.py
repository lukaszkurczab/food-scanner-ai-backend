from typing import Any, Dict, Literal, Optional
from pydantic import BaseModel, Field

class GroundingBundleDto(BaseModel):
    planner: Optional[Dict[str, Any]] = None
    scope: Optional[Dict[str, Any]] = None
    profile_summary: Optional[Dict[str, Any]] = Field(default=None, alias="profileSummary")
    goal_context: Optional[Dict[str, Any]] = Field(default=None, alias="goalContext")
    nutrition_summary: Optional[Dict[str, Any]] = Field(default=None, alias="nutritionSummary")
    comparison: Optional[Dict[str, Any]] = None
    meal_logging_quality: Optional[Dict[str, Any]] = Field(default=None, alias="mealLoggingQuality")
    app_help_context: Optional[Dict[str, Any]] = Field(default=None, alias="appHelpContext")
    chat_summary: Optional[Dict[str, Any]] = Field(default=None, alias="chatSummary")
    thread_memory: Optional[Dict[str, Any]] = Field(default=None, alias="threadMemory")

class PromptBuildInputDto(BaseModel):
    language: Literal["pl", "en"] = "pl"
    response_mode: str = Field(alias="responseMode")
    grounding: GroundingBundleDto
    user_message: str = Field(alias="userMessage")
