import unittest
from ibkr_etl.metadata_monitor_access import COLUMNS, DENIED_COLUMNS, grant_statements


class MetadataMonitorAccessTests(unittest.TestCase):
    def test_only_metadata_column_grants(self):
        statements = grant_statements("ibkr_metadata_monitor")
        self.assertEqual(len(statements), len(COLUMNS)+1)
        for statement in statements[1:]:
            self.assertTrue(statement.startswith("GRANT SELECT ("))
            self.assertNotIn("ALL", statement)
        for table, column in DENIED_COLUMNS:
            self.assertNotIn(column, COLUMNS.get(table, "").split())

    def test_no_arbitrary_role_or_sql(self):
        with self.assertRaises(ValueError):
            grant_statements("PUBLIC")
        with self.assertRaises(ValueError):
            grant_statements("someone; SELECT 1")


if __name__ == "__main__":
    unittest.main()
