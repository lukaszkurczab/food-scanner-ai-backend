from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from google.api_core.exceptions import FailedPrecondition
from pytest import MonkeyPatch

from app.domain.meals.services.meal_query_service import MealQueryService


@dataclass
class _FakeSnapshot:
    id: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return dict(self.payload)


class _FakeQuery:
    def __init__(
        self,
        *,
        datasets: dict[str, list[_FakeSnapshot]],
        filters: list[Any] | None = None,
        fail_day_key: bool = False,
        fail_logged_at: bool = False,
        fail_timestamp: bool = False,
    ) -> None:
        self._datasets = datasets
        self._filters = list(filters or [])
        self._fail_day_key = fail_day_key
        self._fail_logged_at = fail_logged_at
        self._fail_timestamp = fail_timestamp

    def where(self, *, filter: Any) -> "_FakeQuery":
        return _FakeQuery(
            datasets=self._datasets,
            filters=[*self._filters, filter],
            fail_day_key=self._fail_day_key,
            fail_logged_at=self._fail_logged_at,
            fail_timestamp=self._fail_timestamp,
        )

    def stream(self):
        field_paths = {
            getattr(item, "field_path", None)
            for item in self._filters
            if getattr(item, "field_path", None)
        }

        if "dayKey" in field_paths:
            if self._fail_day_key:
                raise FailedPrecondition("missing dayKey index")
            return iter(self._datasets.get("dayKey", []))

        if "loggedAt" in field_paths:
            if self._fail_logged_at:
                raise FailedPrecondition("missing loggedAt index")
            return iter(self._datasets.get("loggedAt", []))

        if "timestamp" in field_paths:
            if self._fail_timestamp:
                raise FailedPrecondition("missing timestamp index")
            return iter(self._datasets.get("timestamp", []))

        return iter(self._datasets.get("all", []))


class _FakeCollection(_FakeQuery):
    pass


async def test_get_meals_in_range_includes_timestamp_records_without_day_key(
    monkeypatch: MonkeyPatch,
) -> None:
    service = MealQueryService()
    collection = _FakeCollection(
        datasets={
            "dayKey": [],
            "loggedAt": [
                _FakeSnapshot(
                    id="meal-1",
                    payload={
                        "loggedAt": "2026-04-23T10:15:00Z",
                        "totals": {"kcal": 520, "protein": 33, "fat": 18, "carbs": 44},
                    },
                )
            ],
            "timestamp": [
                _FakeSnapshot(
                    id="meal-1",
                    payload={
                        "timestamp": "2026-04-23T10:15:00Z",
                        "totals": {"kcal": 520, "protein": 33, "fat": 18, "carbs": 44},
                    },
                )
            ],
            "all": [],
        }
    )
    def _collection_for_user(*, user_id: str) -> _FakeCollection:
        _ = user_id
        return collection

    monkeypatch.setattr(service, "_meals_collection", _collection_for_user)

    records = await service.get_meals_in_range(
        user_id="user-1",
        start_date="2026-04-21",
        end_date="2026-04-27",
        timezone="Europe/Warsaw",
    )

    assert len(records) == 1
    assert records[0].id == "meal-1"
    assert records[0].day_key == "2026-04-23"
    assert records[0].kcal == 520


async def test_get_meals_in_range_ignores_deleted_records_even_when_field_missing_on_others(
    monkeypatch: MonkeyPatch,
) -> None:
    service = MealQueryService()
    collection = _FakeCollection(
        datasets={
            "dayKey": [
                _FakeSnapshot(
                    id="meal-deleted",
                    payload={
                        "dayKey": "2026-04-24",
                        "timestamp": "2026-04-24T10:00:00Z",
                        "deleted": True,
                        "totals": {"kcal": 300, "protein": 10, "fat": 10, "carbs": 30},
                    },
                ),
                _FakeSnapshot(
                    id="meal-active",
                    payload={
                        "dayKey": "2026-04-24",
                        "timestamp": "2026-04-24T12:00:00Z",
                        "totals": {"kcal": 640, "protein": 40, "fat": 20, "carbs": 70},
                    },
                ),
            ],
            "loggedAt": [],
            "timestamp": [],
            "all": [],
        }
    )
    def _collection_for_user(*, user_id: str) -> _FakeCollection:
        _ = user_id
        return collection

    monkeypatch.setattr(service, "_meals_collection", _collection_for_user)

    records = await service.get_meals_in_range(
        user_id="user-1",
        start_date="2026-04-21",
        end_date="2026-04-27",
        timezone="Europe/Warsaw",
    )

    assert [record.id for record in records] == ["meal-active"]


async def test_get_meals_in_range_falls_back_to_collection_scan_when_indexes_missing(
    monkeypatch: MonkeyPatch,
) -> None:
    service = MealQueryService()
    collection = _FakeCollection(
        datasets={
            "dayKey": [],
            "loggedAt": [],
            "timestamp": [],
            "all": [
                _FakeSnapshot(
                    id="meal-1",
                    payload={
                        "timestamp": "2026-04-22T08:00:00Z",
                        "totals": {"kcal": 400, "proteinG": 25, "fatG": 10, "carbsG": 45},
                    },
                )
            ],
        },
        fail_day_key=True,
        fail_logged_at=True,
        fail_timestamp=True,
    )
    def _collection_for_user(*, user_id: str) -> _FakeCollection:
        _ = user_id
        return collection

    monkeypatch.setattr(service, "_meals_collection", _collection_for_user)

    records = await service.get_meals_in_range(
        user_id="user-1",
        start_date="2026-04-21",
        end_date="2026-04-27",
        timezone="Europe/Warsaw",
    )

    assert len(records) == 1
    assert records[0].id == "meal-1"
