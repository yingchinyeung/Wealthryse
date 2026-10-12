import unittest
from unittest.mock import MagicMock
from psycopg.pq import TransactionStatus

from ibkr_etl.evidence_acl import quarantine_evidence_objects, TABLES, FUNCTIONS


class EvidenceAclTests(unittest.TestCase):
    def connection(self, before=(), after=()):
        c = MagicMock()
        c.info.transaction_status = TransactionStatus.INTRANS
        cursor = c.cursor.return_value.__enter__.return_value
        cursor.execute.return_value.fetchall.side_effect = [list(before), list(after)]
        return c, cursor

    def test_no_grants_is_idempotent(self):
        c, cursor = self.connection()
        self.assertEqual(quarantine_evidence_objects(c)["revoked_object_grantees"], 0)
        self.assertEqual(cursor.execute.call_count, 2)
        c.commit.assert_not_called()

    def test_named_grants_are_revoked_for_exact_schema_object(self):
        c, cursor = self.connection([dict(kind="TABLE", name=TABLES[0], grantee=50, recipient="worker")])
        result = quarantine_evidence_objects(c, schema="trial")
        query = cursor.execute.call_args_list[1].args[0].as_string()
        self.assertEqual(query, 'REVOKE ALL ON TABLE "trial"."workflow_stage_evidence" FROM "worker" CASCADE')
        self.assertEqual(result["revoked_object_grantees"], 1)
        self.assertFalse(result["runtime_access_authorized"])

    def test_public_function_grant_is_revoked(self):
        c, cursor = self.connection([dict(kind="FUNCTION", name=FUNCTIONS[0], grantee=0, recipient="PUBLIC")])
        quarantine_evidence_objects(c)
        self.assertIn('FUNCTION "public"."reject_shadow_workflow_mutation"() FROM PUBLIC',
                      cursor.execute.call_args_list[1].args[0].as_string())

    def test_role_identifiers_are_quoted(self):
        c, cursor = self.connection([dict(kind="TABLE", name=TABLES[0], grantee=50, recipient='role"name')])
        quarantine_evidence_objects(c)
        self.assertIn('FROM "role""name" CASCADE', cursor.execute.call_args_list[1].args[0].as_string())

    def test_unexpected_objects_are_not_modified(self):
        c, cursor = self.connection([dict(kind="TABLE", name="nav_snapshots", grantee=50, recipient="worker")])
        with self.assertRaisesRegex(ValueError, "Unexpected protected"): quarantine_evidence_objects(c)
        self.assertEqual(cursor.execute.call_count, 1)

    def test_readback_failure_rolls_back_savepoint(self):
        row = dict(kind="TABLE", name=TABLES[0], grantee=50, recipient="worker")
        c, _ = self.connection([row], [row])
        with self.assertRaisesRegex(ValueError, "readback"): quarantine_evidence_objects(c)
        self.assertIs(c.transaction.return_value.__exit__.call_args.args[0], ValueError)
        c.commit.assert_not_called()

    def test_transaction_required(self):
        c, _ = self.connection()
        c.info.transaction_status = TransactionStatus.IDLE
        with self.assertRaisesRegex(ValueError, "caller-owned"): quarantine_evidence_objects(c)
        c.cursor.assert_not_called()

    def test_invalid_schema_rejected(self):
        c, _ = self.connection()
        with self.assertRaisesRegex(ValueError, "Invalid"): quarantine_evidence_objects(c, schema="public; other")
        c.cursor.assert_not_called()


if __name__ == "__main__":
    unittest.main()
