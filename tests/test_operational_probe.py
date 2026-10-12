"""Contract checks for the independent, metadata-only diagnostic payload."""
import importlib.util
from pathlib import Path
import unittest

from ibkr_etl.benchmark_analytics import CALCULATION_VERSION
from ibkr_etl.risk_snapshots import RISK_ANALYTICS_VERSION


PATH = Path(__file__).resolve().parents[1] / "scripts" / "collect_operational_evidence.py"
spec = importlib.util.spec_from_file_location("operational_probe", PATH)
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)


class OperationalProbeTests(unittest.TestCase):
    def test_metadata_queries_are_read_only_and_explicitly_tenant_scoped(self):
        class Connection:
            queries = []

            def execute(self, query):
                self.queries.append(query)
                return self

            def fetchall(self):
                return [{"read_only":"on"}] if len(self.queries) == 1 else []

        connection = Connection()
        result = probe.collect(connection)
        self.assertTrue(result["inventory_complete"])
        for query in connection.queries:
            self.assertTrue(query.strip().startswith("SELECT"))
            for sensitive in ("payload_json", "error_message", "key_vault_secret_name", "total_nav",
                              "total_text", "annual_rate", "token_fingerprint"):
                self.assertNotIn(sensitive, query)
        for query in connection.queries[1:]:
            self.assertIn("app_tenant_id()", query)

    def test_probe_versions_follow_current_calculators(self):
        source = PATH.read_text(encoding="utf-8")
        self.assertIn("analytics_version='" + RISK_ANALYTICS_VERSION + "'", source)
        self.assertIn("comparison_version='" + CALCULATION_VERSION + "'", source)
        self.assertIn("calculation_version='" + CALCULATION_VERSION + "'", source)
        self.assertIn("default_transaction_read_only=on", source)
        self.assertIn("REPEATABLE READ READ ONLY", source)


if __name__ == "__main__":
    unittest.main()
