from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App metadata
    APP_NAME: str = "Fitaly Food Scanner API"
    DESCRIPTION: str = "Backend API for Fitaly mobile application."
    VERSION: str = "0.1.0"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    API_V2_PREFIX: str = "/api/v2"

    # Runtime environment
    ENVIRONMENT: Literal["local", "development", "staging", "production"] = "local"

    # Integrations
    OPENAI_API_KEY: str = ""
    FIREBASE_PROJECT_ID: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    FIREBASE_CLIENT_EMAIL: str = ""
    FIREBASE_PRIVATE_KEY: str = ""
    FIREBASE_STORAGE_BUCKET: str = ""
    CORS_ORIGINS: str = ""
    SENTRY_DSN: str = ""
    SENTRY_ENVIRONMENT: str = "development"

    # Product limits
    AI_DAILY_LIMIT_FREE: int = Field(default=20, ge=1)
    AI_GATEWAY_ENABLED: bool = True
    AI_REJECT_COST: float = Field(default=0.2, ge=0.0)
    AI_LOCAL_COST: float = Field(default=0.5, ge=0.0)
    AI_GATEWAY_ML_ENABLED: bool = False
    AI_GATEWAY_ML_MODEL_PATH: Path = Path("models/ai_gateway_classifier.joblib")
    AI_GATEWAY_ML_THRESHOLD_OFF_TOPIC: float = Field(default=0.35, ge=0.0, le=1.0)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
