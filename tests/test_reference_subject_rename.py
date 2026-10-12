from __future__ import annotations

import unittest
import json
import sqlite3
import uuid

from ibkr_etl.reference_subject_rename import (
    NEW_DISPLAY_NAME,
    OLD_DISPLAY_NAME,
    reconcile_china_hong_kong_display_name,
    renamed_definition,
)
from ibkr_etl.subject_onboarding import (
    approved_reference_definitions,
    canonical_json,
    configuration_sha256,
)
from ibkr_etl.template_upload import presentation_identity_scope


class ReferenceSubjectRenameTests(unittest.TestCase):
    def test_reference_contract_uses_new_display_name_without_changing_rules(self) -> None:
        definitions = approved_reference_definitions()
        filtered = definitions["hong-kong-china"]
        composite = definitions["alpha-world-ms"]

        self.assertEqual(filtered["display_name"], NEW_DISPLAY_NAME)
        self.assertEqual(filtered["source_account_id"], "U16088606")
        self.assertEqual(filtered["cash_policy"]["opening_cash"]["HKD"], 484300.0)
        self.assertEqual(filtered["position_filter"]["excluded_currencies"], ["TWD"])
        self.assertEqual(composite["components"][0]["component_id"], "hong-kong-china")
        self.assertEqual(composite["components"][0]["component_name"], NEW_DISPLAY_NAME)
        self.assertEqual(composite["components"][0]["coefficient"], 0.91)

    def test_rename_changes_only_exact_structured_labels(self) -> None:
        original = {
            "display_name": OLD_DISPLAY_NAME,
            "subject_id": "hong-kong-china",
            "components": [
                {"component_name": OLD_DISPLAY_NAME, "coefficient": 0.91},
                {"component_name": "Hang Seng Index price index", "coefficient": -0.49},
            ],
            "comment": f"Historical approval for {OLD_DISPLAY_NAME} remains auditable",
        }
        renamed = renamed_definition(original)

        self.assertEqual(renamed["display_name"], NEW_DISPLAY_NAME)
        self.assertEqual(renamed["components"][0]["component_name"], NEW_DISPLAY_NAME)
        self.assertEqual(renamed["subject_id"], "hong-kong-china")
        self.assertEqual(renamed["components"][0]["coefficient"], 0.91)
        self.assertEqual(renamed["comment"], original["comment"])
        self.assertNotEqual(configuration_sha256(original), configuration_sha256(renamed))

    def test_presentation_identity_uses_new_name(self) -> None:
        scope = presentation_identity_scope(
            subject_id="hong-kong-china",
            display_name=NEW_DISPLAY_NAME,
            layout_policy="platform_standard",
        )

        self.assertEqual(scope["source_display_name"], NEW_DISPLAY_NAME)
        self.assertEqual(scope["strategy_name"], "iStrategy: China & Hong Kong")

    def test_reconciliation_is_versioned_and_idempotent(self) -> None:
        tenant = "00000000-0000-0000-0000-000000000001"
        case_id = str(uuid.uuid4())
        definition_id = str(uuid.uuid4())
        actor = "a" * 64
        connection = sqlite3.connect(":memory:")
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            CREATE TABLE platform_subjects(
                tenant_id TEXT, subject_id TEXT, display_name TEXT,
                metadata_json TEXT, updated_at TEXT
            );
            CREATE TABLE subject_calculation_definitions(
                definition_id TEXT, tenant_id TEXT, subject_id TEXT,
                subject_type TEXT, calculation_kind TEXT,
                definition_version INTEGER, configuration_json TEXT,
                configuration_sha256 TEXT, approval_status TEXT,
                effective_from TEXT, effective_to TEXT,
                created_by_identity_sha256 TEXT,
                approved_by_identity_sha256 TEXT,
                created_at TEXT, approved_at TEXT
            );
            CREATE TABLE account_onboarding_approvals(
                approval_id TEXT, tenant_id TEXT, case_id TEXT,
                subject_id TEXT, approval_type TEXT, approval_status TEXT,
                disclosure_version TEXT, destination TEXT, scope_json TEXT,
                scope_sha256 TEXT, approved_by_identity_sha256 TEXT,
                approved_at TEXT, revoked_at TEXT, metadata_json TEXT
            );
            CREATE TABLE account_onboarding_checks(
                tenant_id TEXT, case_id TEXT, check_id TEXT, status TEXT,
                message TEXT, evidence_json TEXT, checked_at TEXT
            );
            CREATE TABLE account_onboarding_cases(
                tenant_id TEXT, case_id TEXT, configuration_json TEXT,
                updated_at TEXT
            );
            CREATE TABLE derived_portfolios(
                tenant_id TEXT, portfolio_id TEXT, name TEXT, updated_at TEXT
            );
            CREATE TABLE synthetic_portfolio_components(
                tenant_id TEXT, portfolio_id TEXT, component_id TEXT,
                component_name TEXT
            );
            """
        )
        new_definition = approved_reference_definitions()["hong-kong-china"]

        def old_label(value):
            if isinstance(value, dict):
                return {key: old_label(item) for key, item in value.items()}
            if isinstance(value, list):
                return [old_label(item) for item in value]
            return OLD_DISPLAY_NAME if value == NEW_DISPLAY_NAME else value

        old_definition = old_label(new_definition)
        old_digest = configuration_sha256(old_definition)
        old_scope = {
            "schema_version": 1,
            "subject_id": "hong-kong-china",
            "subject_type": "filtered_portfolio",
            "display_name": OLD_DISPLAY_NAME,
            "reporting_currency": "USD",
            "history_start_date": "2025-01-20",
            "definition_sha256": old_digest,
            "definition": old_definition,
            "primary_benchmark": {"benchmark_id": "HSI"},
            "secondary_benchmarks": [{"benchmark_id": "SHCOMP"}],
        }
        old_identity = presentation_identity_scope(
            subject_id="hong-kong-china",
            display_name=OLD_DISPLAY_NAME,
            layout_policy="platform_standard",
        )
        connection.execute(
            "INSERT INTO platform_subjects VALUES (?, ?, ?, '{}', ?)",
            (tenant, "hong-kong-china", OLD_DISPLAY_NAME, "2026-09-28"),
        )
        connection.execute(
            """
            INSERT INTO subject_calculation_definitions VALUES (
                ?, ?, 'hong-kong-china', 'filtered_portfolio',
                'filtered_account_with_reconstructed_cash', 1, ?, ?,
                'approved', '2025-01-20', NULL, ?, ?, ?, ?
            )
            """,
            (
                definition_id,
                tenant,
                canonical_json(old_definition),
                old_digest,
                actor,
                actor,
                "2026-09-28",
                "2026-09-28",
            ),
        )
        for approval_type, scope in (
            ("calculation_contract", old_scope),
            ("presentation_identity", old_identity),
        ):
            connection.execute(
                """
                INSERT INTO account_onboarding_approvals VALUES (
                    ?, ?, ?, 'hong-kong-china', ?, 'approved', 'v1',
                    'internal', ?, ?, ?, '2026-09-28', NULL, '{}'
                )
                """,
                (
                    str(uuid.uuid4()),
                    tenant,
                    case_id,
                    approval_type,
                    canonical_json(scope),
                    configuration_sha256(scope),
                    actor,
                ),
            )
        for check_id in (
            "calculation_definition_approved",
            "financial_contract_approved",
            "presentation_identity_approved",
        ):
            connection.execute(
                "INSERT INTO account_onboarding_checks VALUES (?, ?, ?, 'pass', '', '{}', '')",
                (tenant, case_id, check_id),
            )
        connection.execute(
            "INSERT INTO account_onboarding_cases VALUES (?, ?, '{}', '')",
            (tenant, case_id),
        )
        connection.execute(
            "INSERT INTO derived_portfolios VALUES (?, 'hong-kong-china', ?, '')",
            (tenant, OLD_DISPLAY_NAME),
        )
        connection.execute(
            "INSERT INTO synthetic_portfolio_components VALUES (?, 'alpha-world-ms', 'hong-kong-china', ?)",
            (tenant, f"{OLD_DISPLAY_NAME} portfolio"),
        )

        first = reconcile_china_hong_kong_display_name(
            connection, tenant_id=tenant, changed_at="2026-09-29T12:00:00+00:00"
        )
        second = reconcile_china_hong_kong_display_name(
            connection, tenant_id=tenant, changed_at="2026-09-29T12:05:00+00:00"
        )

        self.assertEqual(first["status"], "reconciled")
        self.assertEqual(second["status"], "reconciled")
        self.assertEqual(
            connection.execute(
                "SELECT display_name FROM platform_subjects"
            ).fetchone()[0],
            NEW_DISPLAY_NAME,
        )
        definitions = connection.execute(
            "SELECT definition_version, approval_status, configuration_json "
            "FROM subject_calculation_definitions ORDER BY definition_version"
        ).fetchall()
        self.assertEqual(len(definitions), 2)
        self.assertEqual(definitions[0]["approval_status"], "superseded")
        self.assertEqual(definitions[1]["approval_status"], "approved")
        self.assertEqual(json.loads(definitions[1]["configuration_json"])["display_name"], NEW_DISPLAY_NAME)
        approvals = connection.execute(
            "SELECT approval_type, approval_status, scope_json "
            "FROM account_onboarding_approvals ORDER BY approval_type, approved_at"
        ).fetchall()
        self.assertEqual(len(approvals), 4)
        self.assertEqual(
            sum(row["approval_status"] == "approved" for row in approvals), 2
        )
        current_identity = next(
            json.loads(row["scope_json"])
            for row in approvals
            if row["approval_type"] == "presentation_identity"
            and row["approval_status"] == "approved"
        )
        self.assertEqual(current_identity["strategy_name"], "iStrategy: China & Hong Kong")
        self.assertEqual(
            connection.execute(
                "SELECT component_name FROM synthetic_portfolio_components"
            ).fetchone()[0],
            "China & Hong Kong portfolio",
        )
        connection.close()


if __name__ == "__main__":
    unittest.main()
