"""Monitoring utilities for application startup."""

import logging
import os
import sys

import sentry_sdk
from sentry_sdk.integrations.fastapi import FastApiIntegration
from sentry_sdk.integrations.logging import LoggingIntegration

from app.core.config import settings

logger = logging.getLogger(__name__)


def _running_under_pytest() -> bool:
    return "pytest" in sys.modules or bool(os.getenv("PYTEST_CURRENT_TEST"))


def init_sentry() -> None:
    """Initialize Sentry during application startup when a DSN is configured."""
    if not settings.SENTRY_DSN:
        return
    if settings.ENVIRONMENT == "local":
        logger.info("Sentry initialization skipped in local environment.")
        return
    if _running_under_pytest():
        logger.info("Sentry initialization skipped during pytest execution.")
        return

    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.SENTRY_ENVIRONMENT or settings.ENVIRONMENT,
        release=settings.VERSION,
        traces_sample_rate=0.1,
        integrations=[
            FastApiIntegration(),
            LoggingIntegration(level=None, event_level=logging.ERROR),
        ],
    )
    logger.info("Sentry initialized.")
