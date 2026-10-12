"""Source-only trusted preparation guards; no network or database execution."""
import ast
import base64
import hashlib
import importlib.util
from contextlib import contextmanager
from pathlib import Path
import re
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from ibkr_etl import owner_codex_permissions as permissions


ROOT = Path(__file__).resolve().parents[1]
PATH = ROOT / "scripts/prepare_owner_task_restricted_trial.py"
spec = importlib.util.spec_from_file_location("restricted_preparation", PATH)
prep = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prep)
trial_spec = importlib.util.spec_from_file_location("restricted_contract_trial", ROOT / "scripts/run_owner_task_restricted_trial.py")
trial = importlib.util.module_from_spec(trial_spec)
trial_spec.loader.exec_module(trial)
MIGRATION = (ROOT / "migrations/postgres/059_owner_codex_tasks.sql").read_text(encoding="utf-8")
PASSWORD = "SyntheticOnly_NotARealCredential_" + "a" * 32
VERIFIER = b"SCRAM-SHA-256$4096:" + base64.b64encode(b"s" * 16) + b"$" + base64.b64encode(b"k" * 32) + b":" + base64.b64encode(b"v" * 32)


def metadata(enabled=False):
    rows = []
    for role in prep.ROLES:
        rows.append(dict(rolname=role, rolcanlogin=enabled and role == prep.LOGIN,
            rolinherit=False, rolsuper=False, rolcreatedb=False, rolcreaterole=False,
            rolreplication=False, rolbypassrls=False, rolconnlimit=4 if role == prep.LOGIN else -1, marker=prep.MARKER))
    return {"database": {"datname": prep.DATABASE, "owner": prep.OWNER, "marker": prep.MARKER},
            "roles": rows}


class Raw:
    def __init__(self, database="postgres"):
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=0, host=prep.HOST, dbname=database, user="SYNTH_INSTALLER")
        self.identity = dict(database=database, role="SYNTH_INSTALLER", login="SYNTH_INSTALLER", version="160015")
        self.pgconn = SimpleNamespace(ssl_in_use=True, encrypt_password=Mock(return_value=VERIFIER))
        self.public_connections = []
        self.queries = []
        self.closed = False
        self.transaction_events = []
        self.rowcount = 1
        self.marker_row = {"owner": prep.OWNER, "marker": prep.MARKER}
        self.schema_row = {"owner": prep.OWNER, "marker": prep.MARKER, "fixture_owner": prep.OWNER, "fixture_kind": "r"}

    def execute(self, statement, parameters=None):
        text = statement if type(statement) is str else statement.as_string(None)
        self.queries.append((text, parameters))
        if "server_version_num" in text:
            rows = [self.identity]
        elif "d.datallowconn" in text:
            rows = self.public_connections
        elif "WHERE datname=current_database()" in text:
            rows = [self.marker_row]
        elif "fixture_owner" in text:
            rows = [self.schema_row]
        else:
            rows = []
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else None, fetchall=lambda: rows, rowcount=self.rowcount)

    @contextmanager
    def transaction(self):
        self.transaction_events.append("begin")
        try:
            yield
        except Exception:
            self.transaction_events.append("rollback")
            raise
        else:
            self.transaction_events.append("commit")

    def close(self):
        self.closed = True


class RestrictedPreparationTests(unittest.TestCase):
    def test_import_inert_without_cli_or_credential_or_network_reader(self):
        source = PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(n, ast.If) for n in tree.body))
        self.assertNotRegex(source, r"psycopg\.connect\(|os\.environ|sys\.argv|subprocess\.|requests\.|urllib\.")
        self.assertNotIn("DROP DATABASE", source)
        self.assertNotIn("DROP ROLE", source)
        self.assertNotIn("print(", source)

    def test_scope_is_new_and_different_from_accepted_trial_and_production(self):
        self.assertEqual(prep.DATABASE, "owner_codex_restricted_20261004")
        self.assertNotEqual(prep.DATABASE, "ibkr_staging")
        self.assertNotEqual(prep.DATABASE, "owner_codex_acceptance_20261003")
        self.assertEqual(len(set(prep.ROLES)), 3)
        for name in prep.ROLES + (prep.SCHEMA, prep.DATABASE):
            self.assertRegex(name, r"^[a-z][a-z0-9_]{0,62}$")

    def test_exact_migration_and_synthetic_password_shape(self):
        prep.validate_inputs(MIGRATION, PASSWORD)
        self.assertEqual(hashlib.sha256(MIGRATION.encode()).hexdigest(), prep.MIGRATION_SHA256)
        for text, password in ((MIGRATION + "\n", PASSWORD), (None, PASSWORD),
                               (MIGRATION, "short"), (MIGRATION, "x" * 129),
                               (MIGRATION, "x" * 48 + "\n"), (MIGRATION, 0)):
            with self.subTest(text_type=type(text), password_type=type(password)), self.assertRaises(prep.PreparationError):
                prep.validate_inputs(text, password)

    def test_helper_preserves_actual_001_semantics(self):
        source = (ROOT / "migrations/postgres/001_init.sql").read_text(encoding="utf-8")
        helper = re.search(r"CREATE OR REPLACE FUNCTION app_tenant_id\(\).*?\$\$;", source, re.S).group(0)
        self.assertEqual(prep.TENANT_HELPER_SQL, helper)

    def test_trusted_idle_encrypted_management_connection(self):
        self.assertEqual(prep.bind_trusted_connection(Raw(), "postgres"), "SYNTH_INSTALLER")
        self.assertEqual(prep.bind_trusted_connection(Raw(prep.DATABASE), prep.DATABASE), "SYNTH_INSTALLER")

    def test_trusted_connection_rejects_unregistered_or_active_or_unencrypted(self):
        mutations = (lambda r: setattr(r, "autocommit", False),
                     lambda r: setattr(r.info, "transaction_status", 2),
                     lambda r: setattr(r.info, "host", "unregistered.example"),
                     lambda r: setattr(r.info, "dbname", "ibkr_staging"),
                     lambda r: setattr(r.pgconn, "ssl_in_use", False),
                     lambda r: r.identity.update(database="ibkr_staging"),
                     lambda r: r.identity.update(role=prep.OWNER),
                     lambda r: r.identity.update(login="OTHER"),
                     lambda r: setattr(r.info, "user", "OTHER"),
                     lambda r: r.identity.update(version="150010"),
                     lambda r: r.identity.update(version=160015),
                     lambda r: r.identity.update(extra="unreviewed"))
        for mutate in mutations:
            raw = Raw()
            mutate(raw)
            with self.subTest(mutate=mutate), self.assertRaises(prep.PreparationError):
                prep.bind_trusted_connection(raw, "postgres")
        with self.assertRaises(prep.PreparationError):
            prep.bind_trusted_connection(Raw(), "ibkr_staging")

    def test_exact_metadata_for_disabled_and_enabled_login(self):
        prep.validate_registered_metadata(metadata(False), login_enabled=False)
        prep.validate_registered_metadata(metadata(True), login_enabled=True)

    def test_metadata_type_marker_ownership_and_role_inventory_fail_closed(self):
        mutations = (lambda d: d.update(extra=True),
                     lambda d: d["database"].update(owner=prep.CAPABILITY),
                     lambda d: d["database"].update(marker="other"),
                     lambda d: d.update(roles=[]),
                     lambda d: d["roles"].append(d["roles"][0]),
                     lambda d: d["roles"].__setitem__(0, None))
        for mutate in mutations:
            value = metadata()
            mutate(value)
            with self.subTest(mutate=mutate), self.assertRaises(prep.PreparationError):
                prep.validate_registered_metadata(value, login_enabled=False)
        with self.assertRaises(prep.PreparationError):
            prep.validate_registered_metadata(metadata(), login_enabled=0)

    def test_role_flags_and_integer_bool_confusion_rejected(self):
        for flag in ("rolcanlogin", "rolinherit", "rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls"):
            for bad in (True, 0, None):
                value = metadata()
                value["roles"][0][flag] = bad
                with self.subTest(flag=flag, bad=bad), self.assertRaises(prep.PreparationError):
                    prep.validate_registered_metadata(value, login_enabled=False)
        for bad in (True, 2, "4", None):
            value = metadata()
            value["roles"][0]["rolconnlimit"] = bad
            with self.subTest(bad=bad), self.assertRaises(prep.PreparationError):
                prep.validate_registered_metadata(value, login_enabled=False)

    def test_public_connect_check_is_read_only_and_denies_any_non_target(self):
        raw = Raw()
        prep.assert_public_database_boundary(raw)
        self.assertEqual(len(raw.queries), 1)
        self.assertIn("acldefault('d'", raw.queries[0][0])
        self.assertEqual(raw.queries[0][1], (prep.DATABASE,))
        raw.public_connections = [{"datname": "ibkr_staging"}]
        with self.assertRaises(prep.PreparationError) as error:
            prep.assert_public_database_boundary(raw)
        self.assertEqual(error.exception.code, "non_target_public_database_connect")
        self.assertTrue(all(q.startswith("SELECT") for q, _ in raw.queries))

    def test_existing_names_rejected_before_role_creation(self):
        admin, connector = Raw(), Mock()
        with patch.object(prep, "scope_metadata", return_value=metadata()):
            with self.assertRaises(prep.PreparationError):
                prep.prepare_trial(admin, connector, migration_text=MIGRATION, password=PASSWORD)
        connector.assert_not_called()
        self.assertFalse(any("CREATE" in q for q, _ in admin.queries))

    def test_public_connect_denied_before_any_mutation_or_secret_encryption(self):
        admin, connector = Raw(), Mock()
        admin.public_connections = [{"datname": "postgres"}]
        with patch.object(prep, "scope_metadata", return_value={"database": None, "roles": []}):
            with self.assertRaises(prep.PreparationError):
                prep.prepare_trial(admin, connector, migration_text=MIGRATION, password=PASSWORD)
        connector.assert_not_called()
        admin.pgconn.encrypt_password.assert_not_called()
        self.assertTrue(all(q.startswith("SELECT") for q, _ in admin.queries))

    def test_role_creation_stays_disabled_and_membership_options_explicit(self):
        raw = Raw()
        prep._create_roles(raw)
        creates = [q for q, _ in raw.queries if q.startswith("CREATE ROLE")]
        self.assertEqual(len(creates), 3)
        self.assertTrue(all("NOLOGIN NOINHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS" in q for q in creates))
        grants = [q for q, _ in raw.queries if q.startswith("GRANT")]
        self.assertEqual(len(grants), 2)
        self.assertTrue(all("WITH ADMIN FALSE, INHERIT FALSE, SET TRUE" in q for q in grants))
        self.assertEqual(raw.transaction_events, ["begin", "commit"])

    def test_public_schema_revoke_occurs_under_explicit_owner_role(self):
        raw = Raw(prep.DATABASE)
        prep._initialize(raw, MIGRATION)
        self.assertEqual(raw.queries[0][0], 'SET LOCAL ROLE "' + prep.OWNER + '"')
        self.assertEqual(raw.queries[1][0], "REVOKE ALL ON SCHEMA public FROM PUBLIC")

    def test_real_permission_and_preparation_scope_contracts_match(self):
        self.assertEqual((prep.HOST, prep.DATABASE, prep.SCHEMA, prep.OWNER,
            prep.CAPABILITY, prep.LOGIN, prep.MARKER),
            (permissions.HOST, permissions.DATABASE, permissions.SCHEMA,
            permissions.SCHEMA_OWNER, permissions.CAPABILITY, permissions.LOGIN, permissions.MARKER))
        self.assertEqual(permissions.LOGIN_CONNECTION_LIMIT, 4)
        for enabled in (False, True):
            prep.validate_registered_metadata(metadata(enabled), login_enabled=enabled)
            login = next(r for r in metadata(enabled)["roles"] if r["rolname"] == prep.LOGIN)
            self.assertEqual(login["rolconnlimit"], permissions.LOGIN_CONNECTION_LIMIT)
        self.assertEqual((prep.HOST, prep.DATABASE, prep.SCHEMA, prep.OWNER,
            prep.CAPABILITY, prep.LOGIN, prep.MARKER),
            (trial.HOST, trial.DATABASE, trial.SCHEMA, trial.SCHEMA_OWNER,
            trial.CAPABILITY, trial.LOGIN, trial.MARKER))

    def test_actual_catalog_validator_result_matches_harness_manifest_contract(self):
        # Reuse an explicitly synthetic complete catalog, not a live SQL claim.
        from test_owner_codex_permissions import catalog, INSTALLER
        target = permissions.PermissionTarget()
        result = permissions.validate_catalog(catalog(), target, installer_identity=INSTALLER)
        with patch.object(permissions, "verify_permissions", return_value=result):
            self.assertEqual(trial._permission_manifest(object(), installer_identity=INSTALLER), result)
        self.assertEqual(result["column_privileges_verified"], 84)
        self.assertIs(result["protected_receipts_verified"], False)

    def test_trusted_revocation_acknowledgment_matches_harness_exact_contract(self):
        result = prep.set_synthetic_owner_revoked(Raw(prep.DATABASE))
        self.assertEqual(result, trial.expected_revocation())
        self.assertEqual((prep.TENANT_A, prep.TENANT_B, prep.OWNER_A, prep.OWNER_B,
            prep.WORKER, prep.PRINCIPAL),
            (trial.TENANT_A, trial.TENANT_B, trial.OWNER_A.object_id, trial.OWNER_B.object_id,
            trial.WORKER, trial.SERVICE_PRINCIPAL))

    def test_real_grant_manifest_executes_in_trusted_preparation_contract(self):
        admin, disposable = Raw(), Raw(prep.DATABASE)
        expected = [s.as_string(None) for s in permissions.grant_statements(permissions.PermissionTarget())]
        snapshots = [{"database": None, "roles": []}, metadata(False), metadata(True)]
        with patch.object(prep, "scope_metadata", side_effect=snapshots), \
                patch.object(permissions, "verify_permissions") as verifier:
            result = prep.prepare_trial(admin, lambda: disposable, migration_text=MIGRATION, password=PASSWORD)
        grants = [q for q, _ in disposable.queries if q.startswith("GRANT")]
        self.assertEqual(grants, expected)
        self.assertEqual(len(grants), 11)
        verifier.assert_called_once_with(disposable, permissions.PermissionTarget(),
            installer_identity="SYNTH_INSTALLER", require_fresh_login=False, login_enabled=False)
        self.assertEqual(result["status"], "prepared")
        self.assertIs(result["fresh_login_verified"], False)

    def _prepare(self, *, privilege_error=None, verifier=VERIFIER, enabled_metadata=None):
        admin, disposable = Raw(), Raw(prep.DATABASE)
        admin.pgconn.encrypt_password.return_value = verifier
        module = ModuleType("ibkr_etl.owner_codex_permissions")
        module.PermissionTarget = Mock(return_value="SYNTH_TARGET")
        module.grant_statements = Mock(return_value=("GRANT SYNTHETIC-COLUMN-RIGHTS",))
        module.verify_permissions = Mock(side_effect=privilege_error)
        snapshots = [{"database": None, "roles": []}, metadata(False),
                     metadata(True) if enabled_metadata is None else enabled_metadata]
        with patch.object(prep, "scope_metadata", side_effect=snapshots), patch.dict(sys.modules, {module.__name__: module}):
            try:
                result = prep.prepare_trial(admin, lambda: disposable, migration_text=MIGRATION, password=PASSWORD)
            except Exception as error:
                result = error
        return admin, disposable, module, result

    def test_grants_verified_while_disabled_and_scram_used_before_activation(self):
        admin, disposable, module, result = self._prepare()
        self.assertEqual(result["status"], "prepared")
        self.assertIs(result["fresh_login_verified"], False)
        self.assertIs(result["credential_isolation_verified"], False)
        module.verify_permissions.assert_called_once_with(disposable, "SYNTH_TARGET",
            installer_identity="SYNTH_INSTALLER", require_fresh_login=False, login_enabled=False)
        admin.pgconn.encrypt_password.assert_called_once_with(PASSWORD.encode(), prep.LOGIN.encode(), b"scram-sha-256")
        text = "\n".join(q for q, _ in admin.queries + disposable.queries)
        self.assertNotIn(PASSWORD, text)
        self.assertIn("LOGIN PASSWORD '" + VERIFIER.decode() + "'", text)
        self.assertIn("COMMENT ON SCHEMA", text)
        self.assertTrue(disposable.closed)
        self.assertNotIn(PASSWORD, str(result))

    def test_privilege_failure_does_not_enable_login_or_encrypt_password(self):
        admin, disposable, _, error = self._prepare(privilege_error=RuntimeError("synthetic"))
        self.assertIsInstance(error, prep.PreparationError)
        self.assertEqual(str(error), "trusted_preparation_failed")
        admin.pgconn.encrypt_password.assert_not_called()
        self.assertFalse(any("LOGIN PASSWORD" in q for q, _ in admin.queries))
        self.assertTrue(disposable.closed)

    def test_invalid_scram_blocks_activation(self):
        for verifier in (b"md5incorrect", "SCRAM-SHA-256$incorrect", b"SCRAM-SHA-256$" + b"x" * 600,
                         b"SCRAM-SHA-256$synthetic-verifier", VERIFIER.replace(b"4096", b"1"),
                         VERIFIER.replace(b"4096", b"1000001"), VERIFIER.replace(b"4096", b"04096"),
                         VERIFIER.replace(base64.b64encode(b"s" * 16), base64.b64encode(b"s")),
                         VERIFIER.replace(base64.b64encode(b"k" * 32), b"AAAA=")):
            admin, disposable, _, error = self._prepare(verifier=verifier)
            with self.subTest(verifier_type=type(verifier)):
                self.assertIsInstance(error, prep.PreparationError)
                self.assertFalse(any("LOGIN PASSWORD" in q for q, _ in admin.queries))
                self.assertTrue(disposable.closed)

    def test_enabled_catalog_validation_failure_rolls_back_activation_transaction(self):
        bad = metadata(True)
        bad["roles"][0]["rolsuper"] = True
        admin, disposable, _, error = self._prepare(enabled_metadata=bad)
        self.assertIsInstance(error, prep.PreparationError)
        self.assertTrue(any("LOGIN PASSWORD" in q for q, _ in admin.queries))
        self.assertEqual(admin.transaction_events[-1], "rollback")
        self.assertTrue(disposable.closed)

    def test_provider_errors_do_not_expose_password_or_verifier(self):
        _, _, _, error = self._prepare(privilege_error=RuntimeError("password=" + PASSWORD + VERIFIER.decode()))
        self.assertEqual(str(error), "trusted_preparation_failed")
        raw = Raw(prep.DATABASE)
        raw.execute = Mock(side_effect=RuntimeError("password=" + PASSWORD))
        with self.assertRaises(prep.PreparationError) as caught:
            prep.set_synthetic_owner_revoked(raw)
        self.assertEqual(str(caught.exception), "synthetic_revocation_failed")

    def test_synthetic_revocation_exact_scope_one_row_committed_and_safe_ack(self):
        raw = Raw(prep.DATABASE)
        result = prep.set_synthetic_owner_revoked(raw)
        self.assertEqual(result, {"status": "passed", "database": prep.DATABASE, "schema": prep.SCHEMA,
            "marker": prep.MARKER, "fixture": "owner_task_authority_fixture", "tenant_id": prep.TENANT_A,
            "owner_object_id": prep.OWNER_A, "active": False, "rows_changed": 1,
            "mutation_role": prep.OWNER, "synthetic": True})
        updates = [(q, params) for q, params in raw.queries if q.startswith("UPDATE")]
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0][1], (prep.TENANT_A, prep.OWNER_A))
        self.assertIn("active IS TRUE", updates[0][0])
        self.assertEqual(raw.transaction_events[-1], "commit")

    def test_revocation_denies_marker_mismatch_before_mutation(self):
        raw = Raw(prep.DATABASE)
        raw.marker_row["marker"] = "different"
        with self.assertRaises(prep.PreparationError):
            prep.set_synthetic_owner_revoked(raw)
        self.assertFalse(any(q.startswith("UPDATE") for q, _ in raw.queries))

    def test_revocation_unexpected_row_count_rolls_back(self):
        for count in (0, 2, -1, True):
            raw = Raw(prep.DATABASE)
            raw.rowcount = count
            with self.subTest(count=count), self.assertRaises(prep.PreparationError):
                prep.set_synthetic_owner_revoked(raw)
            self.assertEqual(raw.transaction_events[-1], "rollback")

    def test_revocation_denies_schema_fixture_replacement_before_mutation(self):
        for field, bad in (("marker", "other"), ("owner", prep.CAPABILITY),
                           ("fixture_owner", prep.LOGIN), ("fixture_kind", "v")):
            raw = Raw(prep.DATABASE)
            raw.schema_row[field] = bad
            with self.subTest(field=field), self.assertRaises(prep.PreparationError):
                prep.set_synthetic_owner_revoked(raw)
            self.assertFalse(any(q.startswith("UPDATE") for q, _ in raw.queries))

    def test_preparation_error_codes_do_not_echo_arbitrary_input(self):
        self.assertEqual(str(prep.PreparationError("password=SECRET")), "preparation_contract_failed")
        self.assertEqual(str(prep.PreparationError(None)), "preparation_contract_failed")


if __name__ == "__main__":
    unittest.main()
