#!/usr/bin/env python3
"""Run authenticated smoke flow checks for launch-critical backend contracts."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib import error, parse, request


@dataclass
class HttpResult:
    status: int
    latency_ms: int
    payload: Any


def _normalize_base_url(value: str) -> str:
    return value.rstrip("/")


def _most_recent_sunday() -> str:
    now = datetime.now(UTC).date()
    days_since_sunday = (now.weekday() + 1) % 7
    sunday = now - timedelta(days=days_since_sunday)
    return sunday.isoformat()


def _request_json(
    *,
    method: str,
    url: str,
    body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout_seconds: float = 20.0,
) -> HttpResult:
    payload = None
    req_headers = {"Accept": "application/json"}
    if headers:
        req_headers.update(headers)

    if body is not None:
        payload = json.dumps(body).encode("utf-8")
        req_headers["Content-Type"] = "application/json"

    req = request.Request(url=url, data=payload, method=method.upper(), headers=req_headers)

    started = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout_seconds) as response:  # noqa: S310
            raw = response.read()
            latency_ms = round((time.perf_counter() - started) * 1000)
            parsed_payload: Any = None
            if raw:
                parsed_payload = json.loads(raw.decode("utf-8"))
            return HttpResult(status=response.status, latency_ms=latency_ms, payload=parsed_payload)
    except error.HTTPError as exc:
        raw = exc.read()
        latency_ms = round((time.perf_counter() - started) * 1000)
        parsed_payload: Any = raw.decode("utf-8")
        try:
            parsed_payload = json.loads(parsed_payload)
        except json.JSONDecodeError:
            pass
        return HttpResult(status=exc.code, latency_ms=latency_ms, payload=parsed_payload)


def _expect(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def _expect_latency(latency_ms: int, max_latency_ms: int, label: str) -> None:
    _expect(
        latency_ms <= max_latency_ms,
        f"{label} latency {latency_ms}ms exceeded threshold {max_latency_ms}ms.",
    )


def _build_firebase_sign_in_url(api_key: str) -> str:
    encoded_key = parse.quote(api_key, safe="")
    return (
        "https://identitytoolkit.googleapis.com/v1/accounts:signInWithPassword"
        f"?key={encoded_key}"
    )


def _require_env(name: str) -> str:
    value = (os.getenv(name) or "").strip()
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def _check_export_contract(payload: Any) -> None:
    _expect(isinstance(payload, dict), "Export response payload must be an object.")
    required_keys = {
        "profile",
        "meals",
        "myMeals",
        "chatMessages",
        "notifications",
        "notificationPrefs",
        "feedback",
    }
    missing = sorted(required_keys.difference(payload.keys()))
    _expect(not missing, f"Export payload missing keys: {', '.join(missing)}")


def _check_ai_credits_contract(payload: Any) -> None:
    _expect(isinstance(payload, dict), "AI credits payload must be an object.")
    _expect(
        payload.get("tier") in {"free", "premium"},
        "AI credits payload must contain tier in {free,premium}.",
    )
    _expect(isinstance(payload.get("balance"), int), "AI credits balance must be an integer.")
    _expect(
        isinstance(payload.get("allocation"), int),
        "AI credits allocation must be an integer.",
    )


def _check_weekly_contract(payload: Any, expected_status: int) -> None:
    if expected_status == 403:
        _expect(isinstance(payload, dict), "Weekly report 403 payload must be an object.")
        _expect(
            payload.get("detail") == "WEEKLY_REPORT_PREMIUM_REQUIRED",
            "Weekly report 403 payload must return detail=WEEKLY_REPORT_PREMIUM_REQUIRED.",
        )
        return

    if expected_status == 200:
        _expect(isinstance(payload, dict), "Weekly report payload must be an object.")
        _expect(
            isinstance(payload.get("status"), str),
            "Weekly report 200 payload must contain status.",
        )
        return

    raise RuntimeError(f"Unsupported expected weekly status: {expected_status}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--env",
        required=True,
        help="Environment label used in output (e.g. smoke).",
    )
    parser.add_argument(
        "--base-url",
        required=True,
        help="Backend base URL, e.g. https://fitaly-backend-smoke.up.railway.app",
    )
    parser.add_argument(
        "--max-latency-ms",
        type=int,
        default=5000,
        help="Maximum latency per endpoint check.",
    )
    parser.add_argument(
        "--weekly-expected-status",
        type=int,
        default=403,
        choices=(200, 403),
        help="Expected status for /api/v2/users/me/reports/weekly.",
    )
    parser.add_argument(
        "--summary-output",
        default="",
        help="Optional path to write JSON summary output.",
    )
    args = parser.parse_args()

    env_name = args.env.strip()
    base_url = _normalize_base_url(args.base_url.strip())

    firebase_api_key = _require_env("FIREBASE_WEB_API_KEY")
    smoke_email = _require_env("SMOKE_EXPORT_TEST_EMAIL")
    smoke_password = _require_env("SMOKE_EXPORT_TEST_PASSWORD")

    summary: dict[str, Any] = {
        "env": env_name,
        "baseUrl": base_url,
        "checkedAt": datetime.now(UTC).isoformat(),
        "checks": [],
    }

    sign_in = _request_json(
        method="POST",
        url=_build_firebase_sign_in_url(firebase_api_key),
        body={
            "email": smoke_email,
            "password": smoke_password,
            "returnSecureToken": True,
        },
        timeout_seconds=20.0,
    )
    _expect(sign_in.status == 200, f"Firebase sign-in failed with HTTP {sign_in.status}")
    _expect(isinstance(sign_in.payload, dict), "Firebase sign-in payload must be JSON object.")
    id_token = sign_in.payload.get("idToken")
    _expect(isinstance(id_token, str) and id_token, "Firebase sign-in did not return idToken.")
    summary["checks"].append(
        {"name": "firebase_sign_in", "status": sign_in.status, "latencyMs": sign_in.latency_ms}
    )

    auth_headers = {"Authorization": f"Bearer {id_token}"}

    export_result = _request_json(
        method="GET",
        url=f"{base_url}/api/v1/users/me/export",
        headers=auth_headers,
    )
    _expect(export_result.status == 200, f"Export endpoint returned HTTP {export_result.status}")
    _expect_latency(export_result.latency_ms, args.max_latency_ms, "export")
    _check_export_contract(export_result.payload)
    summary["checks"].append(
        {
            "name": "users_me_export",
            "status": export_result.status,
            "latencyMs": export_result.latency_ms,
        }
    )

    credits_result = _request_json(
        method="GET",
        url=f"{base_url}/api/v1/ai/credits",
        headers=auth_headers,
    )
    _expect(credits_result.status == 200, f"AI credits endpoint returned HTTP {credits_result.status}")
    _expect_latency(credits_result.latency_ms, args.max_latency_ms, "ai_credits")
    _check_ai_credits_contract(credits_result.payload)
    summary["checks"].append(
        {
            "name": "ai_credits",
            "status": credits_result.status,
            "latencyMs": credits_result.latency_ms,
            "tier": credits_result.payload.get("tier") if isinstance(credits_result.payload, dict) else None,
        }
    )

    weekly_end = _most_recent_sunday()
    weekly_result = _request_json(
        method="GET",
        url=f"{base_url}/api/v2/users/me/reports/weekly?weekEnd={weekly_end}",
        headers=auth_headers,
    )
    _expect(
        weekly_result.status == args.weekly_expected_status,
        (
            "Weekly report endpoint returned unexpected status "
            f"{weekly_result.status} (expected {args.weekly_expected_status})."
        ),
    )
    _expect_latency(weekly_result.latency_ms, args.max_latency_ms, "weekly_report")
    _check_weekly_contract(weekly_result.payload, args.weekly_expected_status)
    summary["checks"].append(
        {
            "name": "weekly_report",
            "status": weekly_result.status,
            "latencyMs": weekly_result.latency_ms,
            "weekEnd": weekly_end,
        }
    )

    if args.summary_output:
        with open(args.summary_output, "w", encoding="utf-8") as file:
            json.dump(summary, file, ensure_ascii=False, indent=2)

    print(
        "Flow contract checks passed:",
        f"env={env_name}",
        f"export={export_result.latency_ms}ms",
        f"ai_credits={credits_result.latency_ms}ms",
        f"weekly={weekly_result.latency_ms}ms",
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:  # pragma: no cover - CLI error handling path
        print(f"::error title=Flow contract check failed::{exc}", file=sys.stderr)
        raise
