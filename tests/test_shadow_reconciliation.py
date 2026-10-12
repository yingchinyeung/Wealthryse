from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from ibkr_etl.operating_registry import OperatingTarget, configuration_sha256
from ibkr_etl.shadow_reconciliation import reconcile_shadow_plan


ROOT = Path(__file__).resolve().parents[1]


def _target(subject: str, capability: str, state: str, configuration: dict):
    return OperatingTarget(
        subject_id=subject,
        subject_type="physical_account",
        lifecycle_status="active" if state == "active" else "preview",
        capability=capability,
        operating_state=state,
        effective_from=date(2025, 1, 20),
        configuration=configuration,
        configuration_sha256=configuration_sha256(configuration),
    )


def _manifest() -> dict:
    return {
        "environment": "azure-container-apps",
        "resource_group": "portfolio-platform",
        "observed_at": "2026-09-28T00:00:00Z",
        "jobs": [
            {
                "name": "authoritative-export",
                "capability": "source_export",
                "trigger": "Schedule",
                "cron": "0 8 * * *",
                "retry_limit": 0,
                "subjects": ["U1", "U2"],
            },
            {
                "name": "account-sync",
                "capability": "daily_ingestion",
                "trigger": "Schedule",
                "cron": "10 8 * * *",
                "retry_limit": 0,
                "subjects": ["U1", "U2"],
            },
            {
                "name": "benchmarks",
                "capability": "benchmark_refresh",
                "trigger": "Schedule",
                "cron": "30 8 * * *",
                "retry_limit": 0,
                "subjects": ["U1", "U2"],
            },
            {
                "name": "sectors",
                "capability": "sector_refresh",
                "trigger": "Schedule",
                "cron": "45 8 * * *",
                "retry_limit": 0,
                "subjects": ["U1", "U2"],
            },
            {
                "name": "assurance",
                "capability": "analyst_assurance",
                "trigger": "Schedule",
                "cron": "10 9 * * *",
                "retry_limit": 0,
                "subjects": ["U1"],
            },
            {
                "name": "month-end",
                "capability": "month_end_reporting",
                "trigger": "Schedule",
                "cron": "0 9 1-7 * *",
                "retry_limit": 0,
                "subjects": ["U1"],
            },
        ],
    }


class ShadowReconciliationTests(unittest.TestCase):
    def _targets(self):
        return {
            "daily_ingestion": [
                _target(
                    "U1",
                    "daily_ingestion",
                    "active",
                    {"start_date": "2025-01-20", "source": "existing_v11"},
                ),
                _target(
                    "U2",
                    "daily_ingestion",
                    "preview",
                    {"start_date": "2025-01-20", "source": "existing_v11"},
                ),
            ],
            "benchmark_refresh": [
                _target(
                    "U1",
                    "benchmark_refresh",
                    "active",
                    {"start_date": "2025-01-20"},
                ),
                _target(
                    "U2",
                    "benchmark_refresh",
                    "preview",
                    {"start_date": "2025-01-20"},
                ),
            ],
            "sector_refresh": [
                _target(
                    "U1",
                    "sector_refresh",
                    "active",
                    {"external_approval_required": True},
                ),
                _target(
                    "U2",
                    "sector_refresh",
                    "preview",
                    {"external_approval_required": True},
                ),
            ],
            "analyst_assurance": [
                _target("U1", "analyst_assurance", "active", {}),
            ],
            "month_end_reporting": [
                _target(
                    "U1",
                    "month_end_reporting",
                    "active",
                    {"approval_required": True},
                ),
            ],
        }

    def test_matching_authoritative_jobs_pass_without_queueing_work(self):
        evidence = reconcile_shadow_plan(self._targets(), _manifest())
        self.assertEqual(evidence["status"], "pass")
        self.assertEqual(evidence["mismatches"], [])
        self.assertEqual(evidence["active_subjects"], ["U1"])
        self.assertEqual(evidence["preview_subjects"], ["U2"])
        self.assertEqual(evidence["planned_capability_count"], 10)

    def test_missing_preview_coverage_fails_closed(self):
        manifest = _manifest()
        manifest["jobs"][1]["subjects"] = ["U1"]
        evidence = reconcile_shadow_plan(self._targets(), manifest)
        self.assertEqual(evidence["status"], "fail")
        self.assertIn(
            "subject_coverage_mismatch",
            {item["code"] for item in evidence["mismatches"]},
        )

    def test_nonzero_retry_and_out_of_order_schedule_fail_closed(self):
        manifest = _manifest()
        manifest["jobs"][2]["retry_limit"] = 1
        manifest["jobs"][3]["cron"] = "20 8 * * *"
        evidence = reconcile_shadow_plan(self._targets(), manifest)
        codes = {item["code"] for item in evidence["mismatches"]}
        self.assertIn("nonzero_platform_retry", codes)
        self.assertIn("invalid_schedule_sequence", codes)

    def test_migration_is_tenant_isolated_and_shadow_only(self):
        migration = (
            ROOT
            / "migrations"
            / "postgres"
            / "045_institutional_shadow_reconciliation.sql"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "ALTER TABLE institutional_shadow_reconciliations ENABLE ROW LEVEL SECURITY",
            migration,
        )
        self.assertIn(
            "ALTER TABLE institutional_shadow_reconciliations FORCE ROW LEVEL SECURITY",
            migration,
        )
        script = (ROOT / "scripts" / "reconcile_institutional_shadow.py").read_text(
            encoding="utf-8"
        )
        self.assertNotIn("enqueue_work_plan", script)
        self.assertNotIn("SendRequest", script)
        self.assertIn("work_queue_unchanged", script)


if __name__ == "__main__":
    unittest.main()
