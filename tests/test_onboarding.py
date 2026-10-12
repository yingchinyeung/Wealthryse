from __future__ import annotations

import json
import io
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from ibkr_etl.onboarding import (
    discover_v11_accounts,
    onboarding_case_id,
    parse_account_sync_targets,
)
from ibkr_etl.template_intake import (
    parse_template_manifest,
    register_template_assets,
    verify_template_assets,
)
from ibkr_etl.template_upload import (
    validate_pdf_upload,
    validate_pptx_upload,
    presentation_strategy_contract,
    presentation_identity_scope,
    strategy_name_from_display_name,
    validate_subject_id,
)


class AccountOnboardingTests(unittest.TestCase):
    @staticmethod
    def _presentation(strategy_name: str) -> bytes:
        buffer = io.BytesIO()
        slide = f'''<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main"
 xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">
 <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>{strategy_name}</a:t></a:r></a:p>
 </p:txBody></p:sp></p:spTree></p:cSld></p:sld>'''.encode("utf-8")
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
            archive.writestr("ppt/presentation.xml", "<p:presentation xmlns:p='urn:p'/>")
            archive.writestr("ppt/slides/slide1.xml", slide)
        return buffer.getvalue()

    def test_sync_targets_are_bounded_normalized_and_duplicate_free(self) -> None:
        result = parse_account_sync_targets(
            json.dumps(
                [
                    {"account_id": "u16088571", "start_date": "2025-01-20"},
                    {"account_id": "U16088606", "start_date": "2025-01-20"},
                ]
            ),
            fallback_account_id="U16088571",
            fallback_start_date="2025-01-20",
        )
        self.assertEqual(
            [(item.account_id, item.start_date) for item in result],
            [("U16088571", "2025-01-20"), ("U16088606", "2025-01-20")],
        )
        for value in (
            "[]",
            '[{"account_id":"U16088571","start_date":"2025-01-20","extra":true}]',
            '[{"account_id":"bad","start_date":"2025-01-20"}]',
            '[{"account_id":"U16088571","start_date":"2025-01-20"},'
            '{"account_id":"U16088571","start_date":"2025-01-20"}]',
        ):
            with self.subTest(value=value), self.assertRaises(RuntimeError):
                parse_account_sync_targets(
                    value,
                    fallback_account_id="U16088571",
                    fallback_start_date="2025-01-20",
                )

    def test_blank_sync_target_configuration_preserves_single_account_fallback(self) -> None:
        result = parse_account_sync_targets(
            "",
            fallback_account_id="U16088571",
            fallback_start_date="2025-01-20",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].account_id, "U16088571")

    def test_case_identifier_is_deterministic_and_account_specific(self) -> None:
        values = {
            onboarding_case_id(
                tenant_id="00000000-0000-0000-0000-000000000001",
                source_system="existing_v11",
                source_account_id=account_id,
            )
            for account_id in ("U16088606", "U19025068")
        }
        self.assertEqual(len(values), 2)
        self.assertEqual(
            onboarding_case_id(
                tenant_id="00000000-0000-0000-0000-000000000001",
                source_system="existing_v11",
                source_account_id="U16088606",
            ),
            onboarding_case_id(
                tenant_id="00000000-0000-0000-0000-000000000001",
                source_system="existing_v11",
                source_account_id="U16088606",
            ),
        )

    def test_v11_discovery_separates_client_and_master_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "source.sqlite"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE accounts(
                    account_id TEXT, account_type TEXT, base_currency TEXT,
                    source_json TEXT
                );
                CREATE TABLE nav_snapshots(
                    account_id TEXT, snapshot_date TEXT, asset_class TEXT
                );
                CREATE TABLE position_snapshots(account_id TEXT);
                CREATE TABLE trades(account_id TEXT);
                CREATE TABLE cash_transactions(account_id TEXT);
                INSERT INTO accounts VALUES
                    ('U16088606', 'Institution Client', 'USD', '{"name":"Client"}'),
                    ('U15606638', 'Institution Master', 'USD', '{"name":"Master"}');
                INSERT INTO nav_snapshots VALUES
                    ('U16088606', '2025-01-20', 'TOTAL'),
                    ('U16088606', '2025-01-21', 'TOTAL'),
                    ('U15606638', '2025-01-20', 'TOTAL');
                INSERT INTO position_snapshots VALUES ('U16088606');
                INSERT INTO trades VALUES ('U16088606');
                INSERT INTO cash_transactions VALUES ('U16088606');
                """
            )
            connection.commit()
            connection.close()
            records = {item["account_id"]: item for item in discover_v11_accounts(path)}
        self.assertEqual(records["U16088606"]["eligibility_status"], "eligible")
        self.assertEqual(records["U16088606"]["row_counts"]["nav_days"], 2)
        self.assertEqual(records["U15606638"]["eligibility_status"], "not_portfolio")
        self.assertIn("master", records["U15606638"]["eligibility_reason"].lower())

    def test_onboarding_migration_is_tenant_isolated_and_future_ready(self) -> None:
        migration = Path("migrations/postgres/019_account_onboarding_foundation.sql").read_text(
            encoding="utf-8"
        )
        for table in (
            "platform_subjects",
            "onboarding_source_accounts",
            "account_onboarding_cases",
            "account_onboarding_checks",
            "subject_feature_states",
            "subject_dependencies",
        ):
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("'filtered_portfolio'", migration)
        self.assertIn("'composite_portfolio'", migration)
        self.assertIn("CHECK (subject_id <> depends_on_subject_id)", migration)

    def test_template_registry_is_tenant_isolated_and_keeps_one_current_asset(self) -> None:
        migration = Path(
            "migrations/postgres/023_subject_report_template_registry.sql"
        ).read_text(encoding="utf-8")
        self.assertIn(
            "ALTER TABLE subject_report_template_assets FORCE ROW LEVEL SECURITY",
            migration,
        )
        self.assertIn("WHERE is_current", migration)
        self.assertIn("byte_count <= 52428800", migration)

    def test_document_gate_reconciliation_requires_the_complete_current_package(self) -> None:
        migration = Path(
            "migrations/postgres/048_reconcile_document_intake_gate.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("approval_status IN ('validated', 'active')", migration)
        self.assertIn("report_type = 'factsheet' AND asset_role = 'template'", migration)
        self.assertIn("report_type = 'presentation' AND asset_role = 'template'", migration)
        self.assertIn(
            "report_type = 'factsheet' AND asset_role = 'completed_example'",
            migration,
        )
        self.assertIn("check_id = 'report_documents_validated'", migration)
        self.assertIn("stage = 'reconciliation'", migration)

    def test_template_manifest_requires_all_three_onboarding_documents(self) -> None:
        assets = [
            {
                "report_type": report_type,
                "asset_role": "template",
                "version_label": "owner-supplied-v1",
                "container_name": "internal-reports",
                "blob_name": f"templates/U16088606/intake/{report_type}.{'pdf' if report_type == 'factsheet' else 'pptx'}",
                "content_type": content_type,
                "sha256": "a" * 64,
                "byte_count": 10,
                "metadata": {"source": "owner_upload"},
            }
            for report_type, content_type in (
                ("factsheet", "application/pdf"),
                (
                    "presentation",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                ),
            )
        ]
        assets.append(
            {
                "report_type": "factsheet",
                "asset_role": "completed_example",
                "version_label": "owner-supplied-v1",
                "container_name": "internal-reports",
                "blob_name": "templates/U16088606/intake/completed-factsheet.pdf",
                "content_type": "application/pdf",
                "sha256": "b" * 64,
                "byte_count": 10,
                "metadata": {"source": "owner_upload"},
            }
        )
        payload = json.dumps(
            {"subject_id": "U16088606", "assets": assets}
        ).encode("utf-8")
        parsed = parse_template_manifest(
            payload, subject_id="U16088606", container_name="internal-reports"
        )
        self.assertEqual(len(parsed), 3)
        missing_example = json.loads(payload)
        missing_example["assets"] = missing_example["assets"][:2]
        with self.assertRaisesRegex(RuntimeError, "between 3 and 8"):
            parse_template_manifest(
                json.dumps(missing_example).encode("utf-8"),
                subject_id="U16088606",
                container_name="internal-reports",
            )
        bad = json.loads(payload)
        bad["assets"][0]["blob_name"] = "review/U16088606/factsheet.pdf"
        with self.assertRaisesRegex(RuntimeError, "outside the subject intake prefix"):
            parse_template_manifest(
                json.dumps(bad).encode("utf-8"),
                subject_id="U16088606",
                container_name="internal-reports",
            )

    def test_template_asset_verification_rejects_size_or_hash_mismatch(self) -> None:
        content = b"verified-template"
        assets = parse_template_manifest(
            json.dumps(
                {
                    "subject_id": "U16088606",
                    "assets": [
                        {
                            "report_type": report_type,
                            "asset_role": role,
                            "version_label": "v1",
                            "container_name": "internal-reports",
                            "blob_name": f"templates/U16088606/intake/{name}",
                            "content_type": content_type,
                            "sha256": __import__("hashlib").sha256(content).hexdigest(),
                            "byte_count": len(content),
                            "metadata": {},
                        }
                        for report_type, role, name, content_type in (
                            ("factsheet", "template", "factsheet.pdf", "application/pdf"),
                            (
                                "presentation",
                                "template",
                                "presentation.pptx",
                                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                            ),
                            (
                                "factsheet",
                                "completed_example",
                                "completed-factsheet.pdf",
                                "application/pdf",
                            ),
                        )
                    ],
                }
            ).encode("utf-8"),
            subject_id="U16088606",
            container_name="internal-reports",
        )
        verify_template_assets(assets, download=lambda _container, _blob: content)
        with self.assertRaisesRegex(RuntimeError, "size mismatch"):
            verify_template_assets(assets, download=lambda _container, _blob: b"bad")

    def test_template_registration_passes_document_gate_and_advances_atomically(self) -> None:
        class Cursor:
            def __init__(self, row=None):
                self.row = row

            def fetchone(self):
                return self.row

        class Connection:
            def __init__(self):
                self.calls = []

            def execute(self, statement, parameters=()):
                self.calls.append((statement, parameters))
                if "SELECT lifecycle_status" in statement:
                    return Cursor({"lifecycle_status": "preview"})
                if "SELECT case_id, stage" in statement:
                    return Cursor({"case_id": "case-1", "stage": "template_intake"})
                return Cursor()

        content = b"verified-template"
        assets = parse_template_manifest(
            json.dumps(
                {
                    "subject_id": "U16088606",
                    "assets": [
                        {
                            "report_type": report_type,
                            "asset_role": role,
                            "version_label": "v1",
                            "container_name": "internal-reports",
                            "blob_name": f"templates/U16088606/intake/{name}",
                            "content_type": content_type,
                            "sha256": __import__("hashlib").sha256(content).hexdigest(),
                            "byte_count": len(content),
                            "metadata": {},
                        }
                        for report_type, role, name, content_type in (
                            ("factsheet", "template", "factsheet.pdf", "application/pdf"),
                            (
                                "presentation",
                                "template",
                                "presentation.pptx",
                                "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                            ),
                            (
                                "factsheet",
                                "completed_example",
                                "completed-factsheet.pdf",
                                "application/pdf",
                            ),
                        )
                    ],
                }
            ).encode("utf-8"),
            subject_id="U16088606",
            container_name="internal-reports",
        )
        connection = Connection()
        evidence = register_template_assets(
            connection,
            tenant_id="tenant-1",
            subject_id="U16088606",
            assets=assets,
        )
        self.assertEqual(evidence["asset_count"], 3)
        gate_updates = [
            (statement, parameters)
            for statement, parameters in connection.calls
            if "UPDATE account_onboarding_checks" in statement
        ]
        self.assertEqual(len(gate_updates), 1)
        self.assertIn("status = 'pass'", gate_updates[0][0])
        self.assertEqual(gate_updates[0][1][2:], ("tenant-1", "case-1"))
        self.assertEqual(json.loads(gate_updates[0][1][1]), evidence)
        stage_updates = [
            statement
            for statement, _parameters in connection.calls
            if "UPDATE account_onboarding_cases" in statement
        ]
        self.assertEqual(len(stage_updates), 1)
        self.assertLess(
            connection.calls.index(gate_updates[0]),
            next(
                index
                for index, (statement, _parameters) in enumerate(connection.calls)
                if "UPDATE account_onboarding_cases" in statement
            ),
        )

    def test_upload_validation_rejects_spoofed_or_unsafe_documents(self) -> None:
        self.assertEqual(validate_subject_id("U16088606"), "U16088606")
        with self.assertRaisesRegex(RuntimeError, "identifier"):
            validate_subject_id("../../unsafe")
        with self.assertRaisesRegex(RuntimeError, "valid PDF"):
            validate_pdf_upload("factsheet.pdf", b"not-a-pdf")
        with self.assertRaisesRegex(RuntimeError, "malformed"):
            validate_pptx_upload("presentation.pptx", b"not-a-zip")

    def test_presentation_identity_contract_rejects_another_accounts_strategy(self) -> None:
        content = self._presentation("iStrategy: Greater China")
        upload = validate_pptx_upload("greater-china.pptx", content)
        contract = presentation_strategy_contract(
            upload.content,
            expected_strategy="iStrategy: Greater China",
            layout_policy="subject_template",
        )
        self.assertEqual(contract["strategy_name"], "iStrategy: Greater China")
        self.assertEqual(contract["source_template_sha256"], upload.sha256)
        with self.assertRaisesRegex(RuntimeError, "required strategy title"):
            presentation_strategy_contract(
                upload.content,
                expected_strategy="iStrategy: FOCUS US Tech & Biotech Strategy",
                layout_policy="subject_template",
            )

    def test_alpha_world_subjects_accept_only_the_shared_family_template_exception(self) -> None:
        shared_template = self._presentation(
            "iStrategy Alpha World Enhanced Index"
        )
        for subject_id, expected_strategy in (
            ("alpha-world-ms", "iStrategy: Alpha World MS"),
            ("alpha-world-t", "iStrategy: Alpha World T"),
        ):
            contract = presentation_strategy_contract(
                shared_template,
                expected_strategy=expected_strategy,
                layout_policy="platform_standard",
                subject_id=subject_id,
            )
            self.assertEqual(
                contract["identity_exception_id"],
                "alpha_world_shared_family_template_v1",
            )
            self.assertEqual(
                contract["identity_exception_subject_id"], subject_id
            )
            self.assertEqual(contract["strategy_name"], expected_strategy)

        with self.assertRaisesRegex(RuntimeError, "required strategy title"):
            presentation_strategy_contract(
                shared_template,
                expected_strategy="iStrategy: Greater China",
                layout_policy="platform_standard",
                subject_id="hong-kong-china",
            )

        conflicting_template = self._presentation(
            "iStrategy: Greater China Alpha World"
        )
        with self.assertRaisesRegex(RuntimeError, "another account"):
            presentation_strategy_contract(
                conflicting_template,
                expected_strategy="iStrategy: Alpha World MS",
                layout_policy="platform_standard",
                subject_id="alpha-world-ms",
            )

    def test_alpha_world_shared_template_exception_is_manifest_bound_to_subject(self) -> None:
        presentation = self._presentation("iStrategy Alpha World Enhanced Index")
        contract = presentation_strategy_contract(
            presentation,
            expected_strategy="iStrategy: Alpha World MS",
            layout_policy="platform_standard",
            subject_id="alpha-world-ms",
        )
        contract.update(
            {
                "owner_approval_id": "00000000-0000-4000-8000-000000000001",
                "owner_approval_scope_sha256": "c" * 64,
                "identity_source": "owner_approved_subject_display_name",
            }
        )
        pdf = b"%PDF-1.4\n%%EOF"

        def asset(
            report_type: str,
            role: str,
            name: str,
            content_type: str,
            content: bytes,
            metadata: dict,
        ) -> dict:
            return {
                "report_type": report_type,
                "asset_role": role,
                "version_label": "owner-supplied-v1",
                "container_name": "internal-reports",
                "blob_name": f"templates/alpha-world-ms/intake/{name}",
                "content_type": content_type,
                "sha256": __import__("hashlib").sha256(content).hexdigest(),
                "byte_count": len(content),
                "metadata": metadata,
            }

        payload = {
            "subject_id": "alpha-world-ms",
            "assets": [
                asset(
                    "factsheet",
                    "template",
                    "factsheet.pdf",
                    "application/pdf",
                    pdf,
                    {},
                ),
                asset(
                    "presentation",
                    "template",
                    "presentation.pptx",
                    "application/vnd.openxmlformats-officedocument.presentationml.presentation",
                    presentation,
                    {"presentation_contract": contract},
                ),
                asset(
                    "factsheet",
                    "completed_example",
                    "completed-factsheet.pdf",
                    "application/pdf",
                    pdf,
                    {},
                ),
            ],
        }
        parsed = parse_template_manifest(
            json.dumps(payload).encode("utf-8"),
            subject_id="alpha-world-ms",
            container_name="internal-reports",
        )
        self.assertEqual(len(parsed), 3)

        wrong_subject = json.loads(json.dumps(payload))
        wrong_subject["subject_id"] = "hong-kong-china"
        for item in wrong_subject["assets"]:
            item["blob_name"] = item["blob_name"].replace(
                "alpha-world-ms", "hong-kong-china"
            )
        with self.assertRaisesRegex(RuntimeError, "outside its approved scope"):
            parse_template_manifest(
                json.dumps(wrong_subject).encode("utf-8"),
                subject_id="hong-kong-china",
                container_name="internal-reports",
            )

    def test_presentation_identity_is_derived_from_approved_display_name(self) -> None:
        self.assertEqual(
            strategy_name_from_display_name(
                "U16088606", "U16088606-Greater China"
            ),
            "iStrategy: Greater China",
        )
        self.assertEqual(
            strategy_name_from_display_name(
                "U16088571", "U16088571 (U.S. Technology and Biotechnology)"
            ),
            "iStrategy: U.S. Technology and Biotechnology",
        )
        scope = presentation_identity_scope(
            subject_id="U16088606",
            display_name="U16088606-Greater China",
            layout_policy="subject_template",
        )
        self.assertEqual(scope["strategy_name"], "iStrategy: Greater China")
        self.assertEqual(scope["derivation"], "approved_subject_display_name")
        with self.assertRaisesRegex(RuntimeError, "strategy description"):
            strategy_name_from_display_name("U16088606", "U16088606")


if __name__ == "__main__":
    unittest.main()
