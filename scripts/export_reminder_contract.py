#!/usr/bin/env python3
"""Export canonical Smart Reminders v1 contract from Python type definitions.

Generates ``smart_reminders_v1.contract.json`` — the single source of truth
for cross-repo contract alignment.  Both backend and mobile repos keep a copy
of this file and validate their own types against it.

Usage::

    python scripts/export_reminder_contract.py

The output is written to ``tests/contract_fixtures/smart_reminders_v1.contract.json``
and should be committed.  The mobile repo's copy lives at
``src/__contract_fixtures__/smart_reminders_v1.contract.json``.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import get_args

# Ensure the project root is importable
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.schemas.reminders import (
    NOOP_REASON_CODES,
    SEND_REASON_CODES,
    SUPPRESS_REASON_CODES,
    ReminderDecisionType,
    ReminderKind,
    ReminderReasonCode,
)

TELEMETRY_FIXTURE = PROJECT_ROOT / "tests" / "contract_fixtures" / "smart_reminder_telemetry.json"
OUTPUT_PATH = PROJECT_ROOT / "tests" / "contract_fixtures" / "smart_reminders_v1.contract.json"


def build_contract() -> dict:
    telemetry = json.loads(TELEMETRY_FIXTURE.read_text(encoding="utf-8"))

    return {
        "_doc": (
            "Canonical Smart Reminders v1 contract. "
            "Generated from backend Python types by scripts/export_reminder_contract.py. "
            "Both repos must keep an identical copy — any diff means contract drift."
        ),
        "_version": "v1",
        "decisionTypes": sorted(get_args(ReminderDecisionType)),
        "reminderKinds": sorted(get_args(ReminderKind)),
        "reasonCodes": {
            "all": sorted(get_args(ReminderReasonCode)),
            "send": sorted(SEND_REASON_CODES),
            "suppress": sorted(SUPPRESS_REASON_CODES),
            "noop": sorted(NOOP_REASON_CODES),
        },
        "decisionShape": {
            "requiredFields": [
                "dayKey",
                "computedAt",
                "decision",
                "kind",
                "reasonCodes",
                "scheduledAtUtc",
                "confidence",
                "validUntil",
            ],
            "sendRequires": ["kind", "scheduledAtUtc"],
            "suppressForbids": ["kind", "scheduledAtUtc"],
            "noopForbids": ["kind", "scheduledAtUtc"],
        },
        "telemetry": {
            "allowedEvents": sorted(telemetry["eventNames"]),
            "disallowedEvents": sorted(telemetry["disallowedEventNames"]),
            "propsByEvent": {
                k: sorted(v) for k, v in telemetry["propsByEvent"].items()
            },
        },
    }


def main() -> None:
    contract = build_contract()
    output = json.dumps(contract, indent=2, ensure_ascii=False) + "\n"
    OUTPUT_PATH.write_text(output, encoding="utf-8")
    print(f"Wrote {OUTPUT_PATH}")
    print(f"  decisionTypes:  {contract['decisionTypes']}")
    print(f"  reminderKinds:  {contract['reminderKinds']}")
    print(f"  reasonCodes:    {len(contract['reasonCodes']['all'])} total")
    print(f"  telemetry:      {len(contract['telemetry']['allowedEvents'])} events")


if __name__ == "__main__":
    main()
