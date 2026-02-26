from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # App metadata
    APP_NAME: str = "CaloriAI Food Scanner API"
    DESCRIPTION: str = "Backend API for CaloriAI mobile application."
    VERSION: str = "0.1.0"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    # Runtime environment
    ENVIRONMENT: Literal["local", "development", "staging", "production"] = "local"

    # Integrations
    OPENAI_API_KEY: str = ""
    FIREBASE_PROJECT_ID: str = ""
    GOOGLE_APPLICATION_CREDENTIALS: str = ""
    SENTRY_DSN: str = ""

    # Product limits
    AI_DAILY_LIMIT_FREE: int = Field(default=20, ge=1)

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
