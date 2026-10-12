from __future__ import annotations

import json
import unittest
from pathlib import Path

from ibkr_etl.flex_sources import (
    FlexSourceDefinition,
    SourceAccountBinding,
    circuit_keys_by_credential,
    credential_record,
    discovered_account_summaries,
    partition_report,
    source_execution_plan,
)
from ibkr_etl.parser import parse_flex_xml


MULTI_ACCOUNT_XML = b"""<?xml version='1.0'?>
<FlexQueryResponse><FlexStatements count='2'>
<FlexStatement accountId='U16088571' fromDate='20250120' toDate='20250121'>
<OpenPositions><OpenPosition accountId='U16088571' symbol='AAPL' currency='USD'/></OpenPositions>
</FlexStatement>
<FlexStatement accountId='U16088606' fromDate='20250120' toDate='20250121'>
<OpenPositions><OpenPosition accountId='U16088606' symbol='0700' currency='HKD'/></OpenPositions>
<Trades><Trade accountId='U16088606' symbol='0700' currency='HKD' tradeID='1' tradeDate='20250121'/></Trades>
</FlexStatement>
</FlexStatements></FlexQueryResponse>"""


class FlexSourceTests(unittest.TestCase):
    def test_credential_record_never_serializes_token(self) -> None:
        token = "secret-flex-token-value"
        record = credential_record(
            display_name="Existing IBKR credential",
            key_vault_secret_name="ibkr-flex-source-001",
            token=token,
        )
        self.assertNotIn(token, json.dumps(record))
        self.assertEqual(len(record["token_fingerprint"]), 16)
        self.assertNotIn("token", record)

    def test_one_source_can_bind_multiple_accounts_and_runs_once(self) -> None:
        source = FlexSourceDefinition("source-1", "credential-1", "1609397", "Main Flex query")
        plan = source_execution_plan(
            [source],
            [
                SourceAccountBinding("U16088571", "source-1", "U16088571"),
                SourceAccountBinding("U16088606", "source-1", "U16088606"),
            ],
        )
        self.assertEqual(len(plan), 1)
        self.assertEqual(len(plan[0].bindings), 2)

    def test_multiple_queries_can_share_one_credential_circuit(self) -> None:
        sources = [
            FlexSourceDefinition("source-a", "credential-1", "100", "Daily"),
            FlexSourceDefinition("source-b", "credential-1", "200", "History"),
        ]
        self.assertEqual(
            circuit_keys_by_credential(sources),
            {"credential-1": ("source-a", "source-b")},
        )

    def test_report_is_partitioned_after_one_download(self) -> None:
        report = parse_flex_xml(MULTI_ACCOUNT_XML)
        partitions = partition_report(report)
        self.assertEqual(set(partitions), {"U16088571", "U16088606"})
        self.assertGreater(len(partitions["U16088606"]), len(partitions["U16088571"]))
        summaries = discovered_account_summaries(report)
        self.assertEqual([item["source_account_id"] for item in summaries], ["U16088571", "U16088606"])
        self.assertEqual(summaries[0]["base_currency"], "USD")
        self.assertEqual(summaries[1]["base_currency"], "HKD")

    def test_duplicate_source_account_binding_fails_closed(self) -> None:
        source = FlexSourceDefinition("source-1", "credential-1", "1609397", "Main")
        binding = SourceAccountBinding("U16088571", "source-1", "U16088571")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            source_execution_plan([source], [binding, binding])

    def test_migration_has_rls_and_contains_no_token_value_column(self) -> None:
        migration = Path(
            "migrations/postgres/024_multi_account_flex_sources.sql"
        ).read_text(encoding="utf-8")
        for table in (
            "ibkr_flex_credentials",
            "ibkr_flex_queries",
            "ibkr_flex_source_accounts",
            "subject_flex_source_bindings",
            "ibkr_flex_source_runs",
            "ibkr_flex_source_run_accounts",
            "ibkr_credential_circuit_state",
        ):
            self.assertIn(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY", migration)
        self.assertIn("key_vault_secret_name", migration)
        self.assertNotIn("token_value", migration)
        self.assertIn("PRIMARY KEY (tenant_id, credential_id)", migration)

    def test_validation_queue_and_worker_are_single_attempt_fail_closed(self) -> None:
        migration = Path(
            "migrations/postgres/025_flex_source_validation_queue.sql"
        ).read_text(encoding="utf-8")
        worker = Path("src/ibkr_etl/flex_discovery.py").read_text(encoding="utf-8")
        self.assertIn("attempt_count BETWEEN 0 AND 1", migration)
        self.assertIn("WHERE status IN ('pending', 'running')", migration)
        self.assertIn(
            "ALTER TABLE ibkr_flex_source_validation_requests FORCE ROW LEVEL SECURITY",
            migration,
        )
        self.assertEqual(worker.count(".request_report("), 1)
        self.assertIn("FOR UPDATE OF r SKIP LOCKED", worker)
        self.assertIn("requires_manual_reset", worker)
        self.assertIn("pg_try_advisory_lock", worker)


if __name__ == "__main__":
    unittest.main()
