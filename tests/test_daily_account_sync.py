from __future__ import annotations

import json
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from ibkr_etl.daily_account_sync import validate_production_export_manifest


TENANT = "00000000-0000-0000-0000-000000000001"
HKT = ZoneInfo("Asia/Hong_Kong")


def _manifest(**overrides) -> bytes:
    value = {
        "format_version": 1,
        "generated_at": "2026-09-23T16:02:03+08:00",
        "sqlite_file": "IBKR_Analyst_Snapshot_Latest.sqlite",
        "sqlite_sha256": "a" * 64,
        "tenant_id": TENANT,
        "row_counts": {
            "accounts": 8,
            "nav_snapshots": 1771,
            "position_snapshots": 42967,
        },
    }
    value.update(overrides)
    return json.dumps(value).encode("utf-8")


class DailyAccountSyncTests(unittest.TestCase):
    def test_current_verified_manifest_is_accepted(self) -> None:
        result = validate_production_export_manifest(
            _manifest(),
            expected_tenant_id=TENANT,
            now=datetime(2026, 9, 23, 16, 10, tzinfo=HKT),
        )
        self.assertEqual(result.sqlite_file, "IBKR_Analyst_Snapshot_Latest.sqlite")
        self.assertEqual(result.sqlite_sha256, "a" * 64)

    def test_manifest_fails_closed_for_wrong_tenant_hash_or_unsafe_path(self) -> None:
        now = datetime(2026, 9, 23, 16, 10, tzinfo=HKT)
        for payload in (
            _manifest(tenant_id="another-tenant"),
            _manifest(sqlite_sha256="bad"),
            _manifest(sqlite_file="../all-accounts.sqlite"),
        ):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                validate_production_export_manifest(
                    payload, expected_tenant_id=TENANT, now=now
                )

    def test_manifest_fails_closed_when_stale_future_or_missing_core_rows(self) -> None:
        now = datetime(2026, 9, 23, 16, 10, tzinfo=HKT)
        stale = _manifest(generated_at="2026-09-22T16:02:03+08:00")
        future = _manifest(generated_at="2026-09-23T16:30:03+08:00")
        empty = _manifest(
            row_counts={"accounts": 8, "nav_snapshots": 1, "position_snapshots": 0}
        )
        for payload in (stale, future, empty):
            with self.subTest(payload=payload), self.assertRaises(RuntimeError):
                validate_production_export_manifest(
                    payload,
                    expected_tenant_id=TENANT,
                    now=now,
                    maximum_age=timedelta(hours=12),
                )

    def test_manual_onboarding_may_use_recent_previous_hkt_date(self) -> None:
        result = validate_production_export_manifest(
            _manifest(generated_at="2026-09-23T23:58:03+08:00"),
            expected_tenant_id=TENANT,
            now=datetime(2026, 9, 24, 0, 5, tzinfo=HKT),
            require_current_hkt_date=False,
        )
        self.assertEqual(result.generated_at.date().isoformat(), "2026-09-23")

    def test_manual_onboarding_never_bypasses_maximum_age(self) -> None:
        with self.assertRaises(RuntimeError):
            validate_production_export_manifest(
                _manifest(generated_at="2026-09-22T12:00:00+08:00"),
                expected_tenant_id=TENANT,
                now=datetime(2026, 9, 24, 0, 5, tzinfo=HKT),
                maximum_age=timedelta(hours=12),
                require_current_hkt_date=False,
            )


if __name__ == "__main__":
    unittest.main()
