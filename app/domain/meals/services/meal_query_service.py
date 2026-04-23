from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import cast
from zoneinfo import ZoneInfo

from firebase_admin.exceptions import FirebaseError
from google.api_core.exceptions import FailedPrecondition, GoogleAPICallError, RetryError
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter

from app.core.coercion import coerce_float
from app.core.exceptions import FirestoreServiceError
from app.core.firestore_constants import MEALS_SUBCOLLECTION, USERS_COLLECTION
from app.db.firebase import get_firestore
from app.domain.meals.models.meal_record import MealRecord


class MealQueryService:
    def __init__(self, firestore_client: firestore.Client | None = None) -> None:
        self._db = firestore_client or get_firestore()

    def _meals_collection(self, *, user_id: str) -> firestore.CollectionReference:
        return (
            self._db.collection(USERS_COLLECTION)
            .document(user_id)
            .collection(MEALS_SUBCOLLECTION)
        )

    @staticmethod
    def _parse_date_key(raw_day_key: object, *, timestamp: str, timezone: str) -> str:
        if isinstance(raw_day_key, str):
            text = raw_day_key.strip()
            if text:
                return text

        if timestamp:
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                return dt.astimezone(ZoneInfo(timezone)).date().isoformat()
            except ValueError:
                pass

        return ""

    @staticmethod
    def _extract_totals(raw: object) -> tuple[float, float, float, float]:
        if not isinstance(raw, dict):
            return 0.0, 0.0, 0.0, 0.0
        totals = cast(dict[str, object], raw)
        kcal = coerce_float(totals.get("kcal"))
        protein = coerce_float(
            totals.get("protein") if totals.get("protein") is not None else totals.get("proteinG")
        )
        fat = coerce_float(totals.get("fat") if totals.get("fat") is not None else totals.get("fatG"))
        carbs = coerce_float(
            totals.get("carbs") if totals.get("carbs") is not None else totals.get("carbsG")
        )
        return kcal, protein, fat, carbs

    def _to_meal_record(
        self,
        *,
        meal_id: str,
        payload: dict[str, object],
        timezone: str,
    ) -> MealRecord:
        timestamp = str(payload.get("timestamp") or "").strip()
        day_key = self._parse_date_key(payload.get("dayKey"), timestamp=timestamp, timezone=timezone)
        kcal, protein, fat, carbs = self._extract_totals(payload.get("totals"))
        return MealRecord(
            id=meal_id,
            day_key=day_key,
            timestamp=timestamp,
            meal_count=1,
            kcal=kcal,
            protein_g=protein,
            fat_g=fat,
            carbs_g=carbs,
        )

    @staticmethod
    def _validate_scope(*, start_date: str, end_date: str) -> None:
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
        if end < start:
            raise ValueError("end_date must be on or after start_date")

    @staticmethod
    def _utc_timestamp_bounds(
        *,
        start_date: str,
        end_date: str,
        timezone: str,
    ) -> tuple[str, str]:
        zone = ZoneInfo(timezone)
        utc_zone = ZoneInfo("UTC")
        start_day = datetime.fromisoformat(start_date).date()
        end_day = datetime.fromisoformat(end_date).date()

        start_local = datetime.combine(start_day, time.min, zone)
        end_exclusive_local = datetime.combine(end_day + timedelta(days=1), time.min, zone)

        start_utc = start_local.astimezone(utc_zone).isoformat().replace("+00:00", "Z")
        end_exclusive_utc = (
            end_exclusive_local.astimezone(utc_zone).isoformat().replace("+00:00", "Z")
        )
        return start_utc, end_exclusive_utc

    async def get_meals_in_range(
        self,
        *,
        user_id: str,
        start_date: str,
        end_date: str,
        timezone: str = "Europe/Warsaw",
    ) -> list[MealRecord]:
        self._validate_scope(start_date=start_date, end_date=end_date)
        collection = self._meals_collection(user_id=user_id)
        start_timestamp_utc, end_timestamp_utc = self._utc_timestamp_bounds(
            start_date=start_date,
            end_date=end_date,
            timezone=timezone,
        )
        snapshots_by_id: dict[str, firestore.DocumentSnapshot] = {}
        day_key_query_failed = False
        timestamp_query_failed = False

        try:
            try:
                day_key_query = (
                    collection.where(filter=FieldFilter("dayKey", ">=", start_date))
                    .where(filter=FieldFilter("dayKey", "<=", end_date))
                )
                for snapshot in day_key_query.stream():
                    snapshots_by_id[snapshot.id] = snapshot
            except FailedPrecondition:
                day_key_query_failed = True

            try:
                timestamp_query = (
                    collection.where(filter=FieldFilter("timestamp", ">=", start_timestamp_utc))
                    .where(filter=FieldFilter("timestamp", "<", end_timestamp_utc))
                )
                for snapshot in timestamp_query.stream():
                    snapshots_by_id[snapshot.id] = snapshot
            except FailedPrecondition:
                timestamp_query_failed = True

            if day_key_query_failed and timestamp_query_failed:
                # Graceful fallback when range indexes are temporarily missing.
                for snapshot in collection.stream():
                    snapshots_by_id[snapshot.id] = snapshot
        except (FirebaseError, GoogleAPICallError, RetryError) as exc:
            raise FirestoreServiceError("Failed to query meals in range.") from exc

        records: list[MealRecord] = []
        for snapshot in snapshots_by_id.values():
            payload = dict(snapshot.to_dict() or {})
            if bool(payload.get("deleted")):
                continue
            record = self._to_meal_record(
                meal_id=snapshot.id,
                payload=payload,
                timezone=timezone,
            )
            if not record.day_key:
                continue
            if not (start_date <= record.day_key <= end_date):
                continue
            records.append(record)

        records.sort(key=lambda item: (item.day_key, item.timestamp, item.id))
        return records
