"""Helpers for degrading Firestore queries when composite indexes are missing."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
import logging
from typing import Any

from google.api_core.exceptions import FailedPrecondition

_MISSING_INDEX_MARKER = "requires an index"


def is_missing_index_error(exc: BaseException) -> bool:
    """Return True when Firestore rejected a query because a composite index is missing."""
    if not isinstance(exc, FailedPrecondition):
        return False
    return _MISSING_INDEX_MARKER in str(exc).lower()


def stream_with_missing_index_fallback(
    *,
    indexed_query: Any,
    fallback_query: Any,
    logger: logging.Logger,
    query_name: str,
    extra: Mapping[str, object] | None = None,
) -> Iterable[Any]:
    """Run an indexed query first and fall back to a single-field bounded query if needed."""
    try:
        yield from indexed_query.stream()
        return
    except FailedPrecondition as exc:
        if not is_missing_index_error(exc):
            raise
        logger.warning(
            "Missing Firestore composite index for %s; retrying with degraded bounded query.",
            query_name,
            extra=dict(extra or {}),
        )
        yield from fallback_query.stream()
