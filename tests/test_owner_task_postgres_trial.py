"""Local guards for the synthetic PostgreSQL harness; never connects to Azure."""
import ast
import hashlib
import importlib.util
from pathlib import Path
import re
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import uuid

from ibkr_etl.db import PostgresConnection
from ibkr_etl.owner_codex_tasks import OwnerCodexTasks, OwnerTaskError


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/verify_owner_task_postgres.py"
spec = importlib.util.spec_from_file_location("owner_task_postgres_trial", PATH)
trial = importlib.util.module_from_spec(spec)
spec.loader.exec_module(trial)


class RawFixture:
    def __init__(self):
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=0, host=trial.HOST, dbname=trial.DATABASE)
        self.identity = {"database": trial.DATABASE, "role": trial.ROLE, "session_identity": "TRUSTED_SYNTH_ADMIN",
                         "search_path": trial.SCHEMA + ", pg_catalog", "server_version": "16.0"}
        self.attributes = dict.fromkeys(("rolcanlogin", "rolinherit", "rolsuper", "rolcreatedb",
                                        "rolcreaterole", "rolreplication", "rolbypassrls"), False)
        self.owner, self.memberships, self.temp = True, 0, 0
        self.queries, self.closed, self.committed, self.rolled_back = [], False, 0, 0

    def execute(self, query, parameters=None, **kwargs):
        self.queries.append(str(query))
        if "current_database()" in str(query):
            row = self.identity
        elif "rolcanlogin" in str(query):
            row = self.attributes
        elif "owns_schema" in str(query):
            row = {"owns_schema": self.owner}
        elif "pg_auth_members" in str(query):
            row = {"n": self.memberships}
        elif "pg_my_temp_schema" in str(query):
            row = {"oid": self.temp}
        else:
            row = {}
        return SimpleNamespace(fetchone=lambda: row)

    def commit(self):
        self.committed += 1

    def rollback(self):
        self.rolled_back += 1

    def close(self):
        self.closed = True


class OwnerTaskPostgresTrialTests(unittest.TestCase):
    def test_import_is_inert_and_safe_to_inject_after_executable_code(self):
        source = PATH.read_text(encoding="utf-8")
        compile("injected_marker=True\n" + source, str(PATH), "exec")
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(n, ast.If) for n in tree.body))
        self.assertNotRegex(source, r"psycopg\.connect\(|os\.environ|sys\.argv|\bGRANT\b|\bRESET\s+ROLE\b|\bSET\s+ROLE\s+")
        self.assertNotRegex(source, r"CREATE\s+(?:DATABASE|ROLE|USER|SCHEMA)\b")

    def test_exact_registered_scope_and_six_table_cleanup_allowlist(self):
        self.assertEqual(trial.DATABASE, "owner_codex_acceptance_20261003")
        self.assertEqual(trial.ROLE, "ibkr_owner_codex_trial_20261003")
        self.assertEqual(set(trial.TABLES), {"tenants", "owner_task_authority_fixture", "owner_task_worker_fixture",
                                           "owner_codex_sessions", "owner_codex_tasks", "owner_codex_task_events"})

    def test_exact_reviewed_migration_is_required_before_connection(self):
        text = (ROOT / "migrations/postgres/059_owner_codex_tasks.sql").read_text(encoding="utf-8")
        digest = hashlib.sha256(text.encode()).hexdigest()
        self.assertEqual(digest, trial.EXPECTED_MIGRATION_SHA256)
        with patch.object(trial, "MIGRATION_SQL", text, create=True), patch.object(trial, "MIGRATION_SHA256", digest, create=True):
            self.assertEqual(trial._migration(), text)
        with patch.object(trial, "MIGRATION_SQL", text + "\n", create=True), patch.object(trial, "MIGRATION_SHA256", digest, create=True):
            factory = unittest.mock.Mock()
            result = trial.verify_trial(factory)
            factory.assert_not_called()
            self.assertEqual(result["reason"], "migration_source_mismatch")

    def test_helper_matches_real_default_fallback_in_001(self):
        source = (ROOT / "migrations/postgres/001_init.sql").read_text(encoding="utf-8")
        helper = re.search(r"CREATE OR REPLACE FUNCTION app_tenant_id\(\).*?\$\$;", source, re.S).group(0)
        self.assertEqual(trial.TENANT_HELPER_SQL, helper)

    def test_preflight_accepts_only_fixed_admin_context_effective_role(self):
        self.assertEqual(trial._preflight(RawFixture()), "16.0")

    def test_wrong_host_database_role_path_and_admin_identity_fail_closed(self):
        mutations = (lambda r: setattr(r.info, "host", "unregistered.example"),
                     lambda r: setattr(r.info, "dbname", "ibkr_staging"),
                     lambda r: r.identity.update(database="ibkr_staging"),
                     lambda r: r.identity.update(role="reader"),
                     lambda r: r.identity.update(session_identity=trial.ROLE),
                     lambda r: r.identity.update(search_path="public"))
        for mutate in mutations:
            raw = RawFixture()
            mutate(raw)
            with self.subTest(mutate=mutate), self.assertRaises(trial.TrialFailure):
                trial._preflight(raw)

    def test_role_flags_memberships_existing_temp_and_schema_mismatch_fail(self):
        for flag in RawFixture().attributes:
            raw = RawFixture()
            raw.attributes[flag] = True
            with self.subTest(flag=flag), self.assertRaises(trial.TrialFailure):
                trial._preflight(raw)
        for field, value in (("owner", False), ("memberships", 1), ("temp", 5)):
            raw = RawFixture()
            setattr(raw, field, value)
            with self.subTest(field=field), self.assertRaises(trial.TrialFailure):
                trial._preflight(raw)

    def test_connection_requires_fresh_idle_autocommit_factory_then_disables_it(self):
        raw = RawFixture()
        with trial._connection(lambda: raw) as (_, connection):
            self.assertFalse(raw.autocommit)
            self.assertIs(connection._connection, raw)
            self.assertEqual(raw.committed, 1)
        self.assertTrue(raw.closed)
        self.assertEqual(raw.rolled_back, 1)
        for autocommit, state in ((False, 0), (True, 2)):
            raw = RawFixture()
            raw.autocommit, raw.info.transaction_status = autocommit, state
            with self.assertRaises(trial.TrialFailure):
                trial._preflight(raw)

    def test_service_rejects_autocommit_before_sql_or_authority(self):
        raw, authorize = RawFixture(), unittest.mock.Mock()
        service = OwnerCodexTasks(PostgresConnection(raw), authorize=authorize, verify_receipt=lambda *args: {})
        with self.assertRaises(OwnerTaskError) as error:
            service.create_session(trial.OWNER_A, idempotency_key=str(uuid.uuid4()))
        self.assertEqual(error.exception.code, "transition_denied")
        authorize.assert_not_called()
        self.assertEqual(raw.queries, [])

    def test_synthetic_receipt_verifier_matches_exact_binding_and_is_not_live_proof(self):
        archive = trial.SyntheticArchive()
        binding = {"namespace": trial.AUDIT_NAMESPACE, "phase": "intent", "synthetic": True}
        reference = archive.add(binding)
        receipt = archive(reference, binding)
        self.assertEqual(receipt["binding_sha256"], trial._hash(binding))
        with self.assertRaises(trial.TrialFailure):
            archive(reference, dict(binding, phase="outcome"))

    def test_report_errors_never_reflect_exception_credentials(self):
        with patch.object(trial, "_migration", return_value="synthetic"):
            result = trial.verify_trial(lambda: (_ for _ in ()).throw(RuntimeError("password=SECRET_TOKEN")))
        self.assertEqual(result["reason"], "postgresql_probe_failed")
        self.assertNotIn("SECRET_TOKEN", str(result))
        for field in ("fresh_login_verified", "non_owner_privilege_boundary_verified", "protected_archive_live_verified",
                      "production_migration_installed", "cleanup_performed"):
            self.assertIs(result[field], False)
        self.assertIs(result["receipt_verifier_is_synthetic"], True)

    def test_live_probe_source_preserves_critical_edge_case_checks(self):
        source = PATH.read_text(encoding="utf-8")
        for marker in ("owner.object_id == OWNER_A.object_id", "policy_helper_oid_binding_mismatch",
                       "preexisting_trial_functions", "error_code=NULL", "worker_id=NULL,lease_token=NULL",
                       "denied_request_left_open_transaction", "autocommit_rejection_started_transaction",
                       "threading.Barrier(2)", "cancel_finish_revision_race_failed", "sticky_cancellation_lost_on_expiry",
                       "late_cancel_was_discarded", "CREATE TEMP TABLE", "missing_scope_did_not_fail_closed"):
            self.assertIn(marker, source)


if __name__ == "__main__":
    unittest.main()
