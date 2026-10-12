from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from ibkr_etl.pilot_snapshot import build_pilot_snapshot, validate_pilot_snapshot
from ibkr_etl.pilot_import import import_snapshot
from ibkr_etl.util import canonical_json, sha256_text


class PilotSnapshotTests(unittest.TestCase):
    def _source(self, path: Path) -> None:
        schema = (
            Path(__file__).resolve().parents[1] / "migrations" / "001_init.sql"
        ).read_text(encoding="utf-8")
        connection = sqlite3.connect(path)
        try:
            connection.executescript(schema)
            connection.execute(
                "CREATE TABLE snapshot_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO snapshot_metadata(key, value) VALUES ('generated_at', '2026-09-22T00:00:00+00:00')"
            )
            run_id = connection.execute(
                "INSERT INTO ingestion_runs(run_type, started_at, status) VALUES ('test', '2026-09-22', 'success')"
            ).lastrowid
            for account in ("U16088571", "OTHER"):
                connection.execute(
                    """
                    INSERT INTO accounts(account_id, alias, base_currency, account_type, ib_entity, first_seen_at, last_seen_at, source_json)
                    VALUES (?, '', 'USD', 'Institution Client', 'IBLLC', '2025-01-01', '2026-09-22', '{}')
                    """,
                    (account,),
                )
            connection.execute(
                """
                INSERT INTO instruments(conid, symbol, description, asset_category, currency, first_seen_at, last_seen_at, source_json)
                VALUES ('1', 'TEST', 'Test instrument', 'STK', 'USD', '2025-01-01', '2026-09-22', '{}')
                """
            )
            for account in ("U16088571", "OTHER"):
                for date in ("2025-01-19", "2025-01-20"):
                    connection.execute(
                        """
                        INSERT INTO nav_snapshots(account_id, snapshot_date, currency, asset_class, total_text, row_sha256, last_seen_run_id)
                        VALUES (?, ?, 'USD', 'TOTAL', '100', ?, ?)
                        """,
                        (account, date, f"{account}-{date}", run_id),
                    )
                    connection.execute(
                        """
                        INSERT INTO position_snapshots(account_id, snapshot_date, conid, model_code, currency, quantity_text, market_value_text, row_sha256, last_seen_run_id)
                        VALUES (?, ?, '1', '', 'USD', '1', '100', ?, ?)
                        """,
                        (account, date, f"p-{account}-{date}", run_id),
                    )
            connection.commit()
        finally:
            connection.close()

    def test_build_filters_account_and_start_date(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.sqlite"
            output = root / "pilot.sqlite"
            self._source(source)
            manifest = build_pilot_snapshot(
                source,
                output,
                account_id="U16088571",
                start_date="2025-01-20",
            )
            result = validate_pilot_snapshot(
                output,
                account_id="U16088571",
                start_date="2025-01-20",
            )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["row_counts"]["accounts"], 1)
        self.assertEqual(result["row_counts"]["nav_snapshots"], 1)
        self.assertEqual(result["row_counts"]["position_snapshots"], 1)
        self.assertEqual(manifest["row_counts"], result["row_counts"])

    def test_fixed_generation_time_makes_transfer_byte_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "source.sqlite"
            first = root / "first.sqlite"
            second = root / "second.sqlite"
            self._source(source)
            options = {
                "account_id": "U16088571",
                "start_date": "2025-01-20",
                "generated_at": "2026-09-23T16:02:03+08:00",
            }
            first_manifest = build_pilot_snapshot(source, first, **options)
            second_manifest = build_pilot_snapshot(source, second, **options)
        self.assertEqual(
            first_manifest["output_sha256"], second_manifest["output_sha256"]
        )

    def _evidence_fixture(self, source: Path) -> None:
        self._source(source)
        with closing(sqlite3.connect(source)) as connection, connection:
            connection.execute(
                "UPDATE ingestion_runs SET completed_at='2026-09-22', raw_sha256=?, raw_path='private-do-not-copy'",
                ("a" * 64,),
            )
            samples = [
                ("U16088571", "Transfers", "Transfer", "1", "2025-01-20"),
                ("U16088571", "ConversionRates", "ConversionRate", "2", "2025-01-20"),
                ("OTHER", "Transfers", "Transfer", "3", "2025-01-20"),
                ("U16088571", "Transfers", "Transfer", "4", "2025-01-19"),
                ("U16088571", "AccountInformation", "AccountInformation", "5", "2025-01-20"),
            ]
            for account, section, kind, key, day in samples:
                attrs = {"accountId": account, "reportDate": day, "amount": "100"}
                raw = canonical_json(attrs)
                connection.execute(
                    "INSERT INTO flex_rows(account_id,section,record_type,source_key,report_date,"
                    "row_sha256,data_json,first_seen_run_id,last_seen_run_id) VALUES (?,?,?,?,?,?,?,1,1)",
                    (account, section, kind, key, day, sha256_text(raw), raw),
                )

    def _v3(self, root: Path):
        source, output = root / "source.sqlite", root / "transfer.sqlite"
        self._evidence_fixture(source)
        options = dict(account_id="U16088571", start_date="2025-01-20")
        build_pilot_snapshot(source, output, include_financial_evidence=True, **options)
        return source, output, options

    def test_v3_preserves_only_scoped_financial_evidence_and_lineage(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, options = self._v3(Path(tmp))
            result = validate_pilot_snapshot(output, **options)
            evidence = result["financial_evidence"]
            self.assertEqual(evidence["category_counts"], {"Transfers": 1, "FX": 1})
            self.assertEqual(evidence["incomplete_lineage_rows"], 0)
            self.assertFalse(evidence["reconciliation_authorized"])
            self.assertFalse(evidence["raw_xml_verified"])
            self.assertEqual(evidence["source_completeness"], "unverified")
            with closing(sqlite3.connect(output)) as connection, connection:
                lineage = connection.execute("SELECT source_lineage_json FROM snapshot_flex_evidence").fetchone()[0]
            self.assertNotIn("private-do-not-copy", lineage)
            self.assertIn("a" * 64, lineage)
            self.assertEqual(result["validation_scope"], "snapshot_structure_and_integrity")

    def test_legacy_snapshot_does_not_claim_source_completeness(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, options = self._v3(Path(tmp))
            build_pilot_snapshot(source, output, **options)
            result = validate_pilot_snapshot(output, **options)
            self.assertEqual(result["metadata"]["format_version"], "2")
            self.assertFalse(result["financial_evidence"]["reconciliation_authorized"])
            self.assertIn("Legacy", result["financial_evidence"]["warning"])

    def test_v3_empty_and_missing_source_tables_are_not_verified_no_flows(self):
        for missing in (False, True):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as tmp:
                source, output, options = self._v3(Path(tmp))
                with closing(sqlite3.connect(source)) as connection, connection:
                    connection.execute("DROP TABLE flex_rows" if missing else "DELETE FROM flex_rows")
                build_pilot_snapshot(source, output, include_financial_evidence=True, **options)
                result = validate_pilot_snapshot(output, **options)["financial_evidence"]
                self.assertEqual(result["source_table_available"], not missing)
                self.assertEqual(result["preserved_rows"], 0)
                self.assertEqual(result["source_completeness"], "unverified")

    def test_v3_undated_evidence_is_retained_and_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, options = self._v3(Path(tmp))
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.execute("UPDATE flex_rows SET report_date=NULL WHERE source_key='1'")
                connection.execute("UPDATE ingestion_runs SET raw_sha256=NULL")
            build_pilot_snapshot(source, output, include_financial_evidence=True, **options)
            result = validate_pilot_snapshot(output, **options)["financial_evidence"]
            self.assertEqual(result["undated_rows"], 1)
            self.assertEqual(result["incomplete_lineage_rows"], 2)
            self.assertEqual(result["preserved_rows"], 2)

    def test_v3_detects_value_change_even_with_unchanged_row_count(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, output, options = self._v3(Path(tmp))
            with closing(sqlite3.connect(output)) as connection, connection:
                connection.execute("UPDATE nav_snapshots SET total_text='999'")
            with self.assertRaisesRegex(RuntimeError, "fingerprints"):
                validate_pilot_snapshot(output, **options)

    def test_v3_rejects_corrupt_or_wrong_scope_evidence(self):
        mutations = [
            "UPDATE snapshot_flex_evidence SET account_id='OTHER'",
            "UPDATE snapshot_flex_evidence SET row_sha256='bad'",
            "UPDATE snapshot_flex_evidence SET report_date='2025-01-19'",
            "UPDATE snapshot_flex_evidence SET record_type='AccountInformation', section='AccountInformation'",
            "INSERT INTO snapshot_flex_evidence SELECT * FROM snapshot_flex_evidence LIMIT 1",
            "UPDATE snapshot_flex_evidence SET source_lineage_json='{}'",
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation), tempfile.TemporaryDirectory() as tmp:
                _, output, options = self._v3(Path(tmp))
                with closing(sqlite3.connect(output)) as connection, connection:
                    connection.execute(mutation)
                with self.assertRaises(RuntimeError):
                    validate_pilot_snapshot(output, **options)

    def test_v3_rejects_cross_account_embedded_attributes(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, output, options = self._v3(Path(tmp))
            raw = canonical_json({"accountId": "OTHER"})
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.execute("UPDATE flex_rows SET data_json=?,row_sha256=? WHERE source_key='1'", (raw, sha256_text(raw)))
            with self.assertRaisesRegex(RuntimeError, "another account"):
                build_pilot_snapshot(source, output, include_financial_evidence=True, **options)

    def test_v3_import_fails_before_database_connection_not_silent_discard(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, output, options = self._v3(Path(tmp))
            with self.assertRaisesRegex(RuntimeError, "v3 import is not enabled"):
                import_snapshot(database_url="must-not-connect", local_path=output,
                                tenant_id="test", source_reference="test", **options)

    def test_v3_fixed_time_is_byte_reproducible(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output, options = self._v3(root)
            options.update(include_financial_evidence=True, generated_at="2026-10-01T00:00:00+00:00")
            first = build_pilot_snapshot(source, output, **options)
            second = build_pilot_snapshot(source, root / "second.sqlite", **options)
            self.assertEqual(first["output_sha256"], second["output_sha256"])

    def test_rejects_unknown_format_and_null_normalized_account(self):
        for sql in ("UPDATE snapshot_metadata SET value='99' WHERE key='format_version'",
                    "UPDATE nav_snapshots SET account_id=NULL"):
            with tempfile.TemporaryDirectory() as tmp:
                _, output, options = self._v3(Path(tmp))
                with closing(sqlite3.connect(output)) as connection, connection:
                    connection.execute(sql)
                with self.assertRaises(RuntimeError):
                    validate_pilot_snapshot(output, **options)

    def test_v3_does_not_silently_drop_malformed_source_dates(self):
        for sql in ("UPDATE nav_snapshots SET snapshot_date='0000-bad' WHERE snapshot_date='2025-01-20'",
                    "UPDATE flex_rows SET report_date='0000-bad' WHERE source_key='1'"):
            with tempfile.TemporaryDirectory() as tmp:
                source, output, options = self._v3(Path(tmp))
                with closing(sqlite3.connect(source)) as connection, connection:
                    connection.execute(sql)
                with self.assertRaises((RuntimeError, ValueError)):
                    build_pilot_snapshot(source, output, include_financial_evidence=True, **options)

    def test_failed_build_preserves_previous_validated_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source, output, options = self._v3(root)
            before = output.read_bytes()
            with closing(sqlite3.connect(source)) as connection, connection:
                connection.execute("UPDATE flex_rows SET row_sha256='bad' WHERE source_key='1'")
            with self.assertRaisesRegex(RuntimeError, "hash mismatch"):
                build_pilot_snapshot(source, output, include_financial_evidence=True, **options)
            self.assertEqual(output.read_bytes(), before)
            self.assertEqual(list(root.glob(".snapshot-*")), [])

    def test_v3_rejects_duplicate_normalized_identity(self):
        with tempfile.TemporaryDirectory() as tmp:
            _, output, options = self._v3(Path(tmp))
            with closing(sqlite3.connect(output)) as connection, connection:
                connection.execute("INSERT INTO nav_snapshots SELECT * FROM nav_snapshots")
            with self.assertRaisesRegex(RuntimeError, "duplicate/missing identities"):
                validate_pilot_snapshot(output, **options)


if __name__ == "__main__":
    unittest.main()
