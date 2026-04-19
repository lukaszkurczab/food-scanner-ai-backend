from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.domain.meals.services.meal_query_service import MealQueryService


class NutritionSummaryService:
    def __init__(self, meal_query_service: MealQueryService) -> None:
        self.meal_query_service = meal_query_service

    @staticmethod
    def _enumerate_days(*, start_date: str, end_date: str) -> list[str]:
        start = datetime.fromisoformat(start_date).date()
        end = datetime.fromisoformat(end_date).date()
        if end < start:
            raise ValueError("end_date must be on or after start_date")
        days: list[str] = []
        cursor = start
        while cursor <= end:
            days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        return days

    @staticmethod
    def _coverage_level(*, days_in_period: int, days_with_entries: int) -> str:
        if days_in_period <= 0 or days_with_entries <= 0:
            return "none"
        ratio = days_with_entries / days_in_period
        if ratio < 0.40:
            return "low"
        if ratio < 0.75:
            return "medium"
        return "high"

    @staticmethod
    def _reliability(*, coverage_level: str) -> dict[str, str]:
        if coverage_level in {"none", "low"}:
            return {"summaryConfidence": "low", "reason": "insufficient_logged_days"}
        if coverage_level == "medium":
            return {"summaryConfidence": "medium", "reason": "partial_logging_coverage"}
        return {"summaryConfidence": "high", "reason": "sufficient_logging_coverage"}

    @staticmethod
    def _build_signals(
        *,
        coverage_level: str,
        total_kcal: float,
        total_protein_g: float,
        days_with_entries: int,
    ) -> list[str]:
        if coverage_level == "none":
            return ["no_logged_meals"]
        if coverage_level == "low":
            return ["logging_sparse"]

        signals: list[str] = []
        if coverage_level == "medium":
            signals.append("logging_partial")
        if coverage_level == "high":
            signals.append("logging_consistent")

        denominator = max(days_with_entries, 1)
        avg_kcal = total_kcal / denominator
        avg_protein = total_protein_g / denominator

        if avg_kcal < 1200:
            signals.append("kcal_low_vs_reference")
        elif avg_kcal > 3000:
            signals.append("kcal_high_vs_reference")

        if avg_protein < 60:
            signals.append("protein_low_vs_reference")
        elif avg_protein > 160:
            signals.append("protein_high_vs_reference")

        return signals

    @staticmethod
    def _default_partial(period_type: str, timezone: str, end_date: str) -> bool:
        if period_type not in {"today", "calendar_week", "rolling_7d"}:
            return False
        today = datetime.now(ZoneInfo(timezone)).date().isoformat()
        return end_date == today

    async def build_period_summary(
        self,
        *,
        user_id: str,
        start_date: str,
        end_date: str,
        timezone: str,
        period_type: str,
        is_partial: bool | None = None,
    ) -> dict:
        days = self._enumerate_days(start_date=start_date, end_date=end_date)
        meals = await self.meal_query_service.get_meals_in_range(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            timezone=timezone,
        )

        per_day: dict[str, dict[str, float | int]] = {
            day: {
                "mealCount": 0,
                "kcal": 0.0,
                "proteinG": 0.0,
                "fatG": 0.0,
                "carbsG": 0.0,
            }
            for day in days
        }

        total_kcal = 0.0
        total_protein = 0.0
        total_fat = 0.0
        total_carbs = 0.0

        for meal in meals:
            if meal.day_key not in per_day:
                continue
            day_bucket = per_day[meal.day_key]
            day_bucket["mealCount"] = int(day_bucket["mealCount"]) + 1
            day_bucket["kcal"] = float(day_bucket["kcal"]) + meal.kcal
            day_bucket["proteinG"] = float(day_bucket["proteinG"]) + meal.protein_g
            day_bucket["fatG"] = float(day_bucket["fatG"]) + meal.fat_g
            day_bucket["carbsG"] = float(day_bucket["carbsG"]) + meal.carbs_g

            total_kcal += meal.kcal
            total_protein += meal.protein_g
            total_fat += meal.fat_g
            total_carbs += meal.carbs_g

        days_with_entries = sum(1 for item in per_day.values() if int(item["mealCount"]) > 0)
        coverage_level = self._coverage_level(
            days_in_period=len(days),
            days_with_entries=days_with_entries,
        )
        reliability = self._reliability(coverage_level=coverage_level)
        signals = self._build_signals(
            coverage_level=coverage_level,
            total_kcal=total_kcal,
            total_protein_g=total_protein,
            days_with_entries=days_with_entries,
        )

        if is_partial is None:
            is_partial = self._default_partial(period_type, timezone, end_date)

        daily_breakdown = []
        for day in days:
            day_bucket = per_day[day]
            daily_breakdown.append(
                {
                    "date": day,
                    "mealCount": int(day_bucket["mealCount"]),
                    "kcal": round(float(day_bucket["kcal"]), 2),
                    "proteinG": round(float(day_bucket["proteinG"]), 2),
                    "fatG": round(float(day_bucket["fatG"]), 2),
                    "carbsG": round(float(day_bucket["carbsG"]), 2),
                }
            )

        return {
            "period": {
                "type": period_type,
                "startDate": start_date,
                "endDate": end_date,
                "timezone": timezone,
                "isPartial": bool(is_partial),
            },
            "loggingCoverage": {
                "daysInPeriod": len(days),
                "daysWithEntries": days_with_entries,
                "mealCount": len(meals),
                "coverageLevel": coverage_level,
            },
            "totals": {
                "kcal": round(total_kcal, 2),
                "proteinG": round(total_protein, 2),
                "fatG": round(total_fat, 2),
                "carbsG": round(total_carbs, 2),
            },
            "dailyBreakdown": daily_breakdown,
            "signals": signals,
            "reliability": reliability,
        }

    async def build_logging_quality(
        self,
        *,
        user_id: str,
        start_date: str,
        end_date: str,
        timezone: str,
    ) -> dict:
        days = self._enumerate_days(start_date=start_date, end_date=end_date)
        meals = await self.meal_query_service.get_meals_in_range(
            user_id=user_id,
            start_date=start_date,
            end_date=end_date,
            timezone=timezone,
        )
        days_with_entries = len({meal.day_key for meal in meals if meal.day_key})
        coverage_level = self._coverage_level(
            days_in_period=len(days),
            days_with_entries=days_with_entries,
        )
        return {
            "coverageLevel": coverage_level,
            "daysWithEntries": days_with_entries,
            "missingDays": max(len(days) - days_with_entries, 0),
            "canSupportTrendAnalysis": coverage_level in {"medium", "high"},
        }
