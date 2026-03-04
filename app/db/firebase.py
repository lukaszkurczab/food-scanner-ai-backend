"""Firebase Admin and Firestore initialization helpers.

This module keeps SDK initialization in one place so the rest of the
application can depend on a single, stable entry point for Firestore access.
`get_firestore()` is memoized to avoid rebuilding the Firestore client on every
call and to prevent repeated Firebase initialization work during the process
lifetime.
"""

from functools import lru_cache
import logging

import firebase_admin
from firebase_admin import credentials, firestore as admin_firestore, storage as admin_storage
from google.cloud import firestore
from google.cloud.storage import bucket as storage_bucket

from app.core.config import settings

logger = logging.getLogger(__name__)


def _build_firebase_credential() -> credentials.Base:
    """Build Firebase credentials from env vars or a local service account file."""
    if settings.FIREBASE_CLIENT_EMAIL and settings.FIREBASE_PRIVATE_KEY:
        normalized_private_key = settings.FIREBASE_PRIVATE_KEY.replace("\\n", "\n")
        service_account_info = {
            "type": "service_account",
            "project_id": settings.FIREBASE_PROJECT_ID,
            "client_email": settings.FIREBASE_CLIENT_EMAIL,
            "private_key": normalized_private_key,
            "token_uri": "https://oauth2.googleapis.com/token",
        }
        return credentials.Certificate(service_account_info)

    if settings.GOOGLE_APPLICATION_CREDENTIALS:
        return credentials.Certificate(settings.GOOGLE_APPLICATION_CREDENTIALS)

    raise ValueError(
        "Firebase credentials are not configured. Set FIREBASE_CLIENT_EMAIL and "
        "FIREBASE_PRIVATE_KEY, or GOOGLE_APPLICATION_CREDENTIALS."
    )


def init_firebase() -> firebase_admin.App:
    """Initialize Firebase Admin once and return the active app instance."""
    if firebase_admin._apps:
        return firebase_admin.get_app()

    try:
        credential = _build_firebase_credential()
        options = {"projectId": settings.FIREBASE_PROJECT_ID}
        storage_bucket = settings.FIREBASE_STORAGE_BUCKET.strip()
        if not storage_bucket and settings.FIREBASE_PROJECT_ID:
            storage_bucket = f"{settings.FIREBASE_PROJECT_ID}.appspot.com"
        if storage_bucket:
            options["storageBucket"] = storage_bucket
        return firebase_admin.initialize_app(
            credential=credential,
            options=options,
        )
    except Exception:
        logger.exception("Failed to initialize Firebase Admin SDK.")
        raise


@lru_cache()
def get_firestore() -> firestore.Client:
    """Return a memoized Firestore client for the configured Firebase project.

    Memoization ensures the client is created only once per process, which keeps
    SDK startup centralized and avoids duplicate initialization paths across the
    application.
    """
    app = init_firebase()
    return admin_firestore.client(app=app)


@lru_cache()
def get_storage_bucket() -> storage_bucket.Bucket:
    app = init_firebase()
    return admin_storage.bucket(app=app)
