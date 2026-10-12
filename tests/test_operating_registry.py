from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

from ibkr_etl.operating_registry import (
    FlexSubjectBinding,
    WorkClaim,
    configuration_sha256,
    fail_work,
    plan_flex_work,
    plan_month_end_work,
    validate_operating_target,
)


ROOT = Path(__file__).resolve().parents[1]


class OperatingRegistryTests(unittest.TestCase):
    def _row(self, **overrides):
        configuration = overrides.pop("configuration", {"start_date": "2025-01-20"})
        row = {
            "subject_id": "U12345678",
            "subject_type": "physical_account",
            "lifecycle_status": "active",
            "capability": "daily_ingestion",
            "operating_state": "active",
            "effective_from": "2025-01-20",
            "configuration_json": configuration,
            "configuration_sha256": configuration_sha256(configuration),
        }
        row.update(overrides)
        return row

    def test_registration_is_integrity_bound_and_lifecycle_gated(self):
        target = validate_operating_target(self._row())
        self.assertEqual(target.subject_id, "U12345678")
        with self.assertRaisesRegex(RuntimeError, "integrity check failed"):
            validate_operating_target(self._row(configuration_sha256="0" * 64))
        with self.assertRaisesRegex(RuntimeError, "non-active subject"):
            validate_operating_target(self._row(lifecycle_status="onboarding"))
        preview = validate_operating_target(
            self._row(lifecycle_status="onboarding", operating_state="preview")
        )
        self.assertEqual(preview.operating_state, "preview")

    def test_one_broker_request_per_source_and_subject_parallelism(self):
        bindings = [
            FlexSubjectBinding("cred-a", "source-a", "U1", "U1", date(2025, 1, 20)),
            FlexSubjectBinding("cred-a", "source-a", "U2", "U2", date(2025, 1, 20)),
            FlexSubjectBinding("cred-a", "source-b", "U3", "U3", date(2025, 1, 20)),
            FlexSubjectBinding("cred-b", "source-c", "U4", "U4", date(2025, 1, 20)),
        ]
        work = plan_flex_work(bindings, run_date=date(2026, 9, 28))
        requests = [item for item in work if item["work_kind"] == "flex_statement_request"]
        self.assertEqual(len(requests), 3)
        self.assertTrue(all(item["maximum_attempts"] == 1 for item in requests))
        self.assertEqual(
            [item["ordering_key"] for item in requests],
            ["credential:cred-a", "credential:cred-a", "credential:cred-b"],
        )
        downstream = [item for item in work if item["work_kind"] != "flex_statement_request"]
        self.assertEqual(len(downstream), 12)
        self.assertEqual(
            {item["ordering_key"] for item in downstream},
            {"subject:U1", "subject:U2", "subject:U3", "subject:U4"},
        )
        self.assertEqual(len({item["idempotency_key"] for item in work}), len(work))

    def test_duplicate_subject_binding_fails_closed(self):
        with self.assertRaisesRegex(RuntimeError, "multiple active Flex bindings"):
            plan_flex_work(
                [
                    FlexSubjectBinding("cred-a", "source-a", "U1", "U1", date(2025, 1, 20)),
                    FlexSubjectBinding("cred-b", "source-b", "U1", "U1", date(2025, 1, 20)),
                ],
                run_date=date(2026, 9, 28),
            )

    def test_month_end_work_is_account_independent_and_idempotent(self):
        first = validate_operating_target(
            self._row(
                subject_id="U1",
                capability="month_end_reporting",
                configuration={"approval_required": True},
                configuration_sha256=configuration_sha256(
                    {"approval_required": True}
                ),
            )
        )
        second = validate_operating_target(
            self._row(
                subject_id="U2",
                capability="month_end_reporting",
                configuration={"approval_required": True},
                configuration_sha256=configuration_sha256(
                    {"approval_required": True}
                ),
            )
        )
        plan = plan_month_end_work([second, first], period_end=date(2026, 8, 31))
        self.assertEqual([item["subject_id"] for item in plan], ["U1", "U2"])
        self.assertTrue(all("U16088571" not in item["idempotency_key"] for item in plan))
        self.assertEqual(len({item["idempotency_key"] for item in plan}), 2)

    def test_migration_is_tenant_isolated_and_excludes_secret_storage(self):
        migration = (ROOT / "migrations" / "postgres" / "043_institutional_onboarding_control_plane.sql").read_text(encoding="utf-8")
        for table in (
            "subject_operating_registrations",
            "workflow_outbox",
            "institutional_work_items",
        ):
            self.assertIn(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY", migration)
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", migration)
        self.assertNotIn("token_value", migration.lower())
        self.assertNotIn("client_secret", migration.lower())
        self.assertIn("maximum_attempts=1", migration)

    def test_flex_failure_is_always_dead_lettered_without_retry(self):
        class Cursor:
            rowcount = 1

        class Connection:
            def __init__(self):
                self.sql = ""

            def execute(self, sql, parameters):
                self.sql = sql
                self.parameters = parameters
                return Cursor()

        connection = Connection()
        claim = WorkClaim(
            work_item_id="work-1",
            work_kind="flex_statement_request",
            ordering_key="credential:one",
            idempotency_key="flex:one:2026-09-28",
            subject_id=None,
            flex_source_id="source-1",
            payload={},
            attempt_count=1,
            maximum_attempts=1,
        )
        status = fail_work(
            connection,
            tenant_id="tenant",
            claim=claim,
            worker_id="worker",
            error_code="1001",
            error_message="Statement unavailable",
        )
        self.assertEqual(status, "dead_letter")
        self.assertIn("status = 'dead_letter'", connection.sql)
        self.assertNotIn("available_at", connection.sql)


if __name__ == "__main__":
    unittest.main()
