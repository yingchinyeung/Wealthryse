"""Local exact metadata ACL specification guards; no database or cloud access."""
import copy
from pathlib import Path
from types import SimpleNamespace
import unittest

from psycopg import sql
from ibkr_etl import owner_codex_permissions as permissions


SCHEMA = "owner_codex_059_restricted"
OWNER = "ibkr_owner_codex_schema_20261004"
CAP = "ibkr_owner_codex_dml_20261004"
LOGIN = "ibkr_owner_codex_login_20261004"
DATABASE = "owner_codex_restricted_20261004"
INSTALLER = "trusted_installer"
READ = {
    "owner_codex_sessions": "tenant_id owner_object_id session_id idempotency_key created_at".split(),
    "owner_codex_tasks": "tenant_id owner_object_id task_id session_id idempotency_key task_kind subject_ids input_sha256 request_sha256 authority_sha256 state revision worker_id lease_token lease_expires_at cancel_requested_at intent_receipt_sha256 outcome_receipt_sha256 result_sha256 error_code delivery_available created_at updated_at".split(),
    "owner_codex_task_events": "tenant_id owner_object_id task_id event_no state code occurred_at".split(),
    "owner_task_authority_fixture": "tenant_id owner_object_id active role_name registered_subject_ids authorized_subject_ids".split(),
    "owner_task_worker_fixture": "tenant_id worker_id service_principal_id enabled".split(),
}
WRITE = {
    "owner_codex_sessions": READ["owner_codex_sessions"],
    "owner_codex_tasks": "tenant_id owner_object_id task_id session_id idempotency_key task_kind subject_ids input_sha256 request_sha256 authority_sha256 state revision delivery_available created_at updated_at".split(),
    "owner_codex_task_events": READ["owner_codex_task_events"],
}
CHANGE = {"owner_codex_tasks": "state revision worker_id lease_token lease_expires_at cancel_requested_at intent_receipt_sha256 outcome_receipt_sha256 result_sha256 error_code delivery_available updated_at".split()}


def catalog(*, login_enabled=True):
    result = {"identity": {"host": "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com", "database": DATABASE,
        "authenticated_user": LOGIN, "session_user": LOGIN, "current_user": CAP, "search_path": SCHEMA + ", pg_catalog"}}
    result["roles"] = [{"role": name, "rolcanlogin": login_enabled if name == LOGIN else False,
        "rolinherit": False, "rolsuper": False, "rolcreatedb": False, "rolcreaterole": False,
        "rolreplication": False, "rolbypassrls": False, "rolconnlimit": 4 if name == LOGIN else -1} for name in (OWNER, CAP, LOGIN)]
    result["installer"] = {"role": INSTALLER, "superuser": False}
    result["bootstrap_role"] = "postgres"
    result["memberships"] = [{"member": LOGIN, "role": CAP, "grantor": INSTALLER, "admin_option": False,
        "inherit_option": False, "set_option": True}, {"member": INSTALLER, "role": OWNER, "grantor": INSTALLER,
        "admin_option": False, "inherit_option": False, "set_option": True}]
    result["memberships"].extend({"member": INSTALLER, "role": role, "grantor": "postgres", "admin_option": True,
        "inherit_option": False, "set_option": False} for role in (OWNER, CAP, LOGIN))
    result["columns"] = []
    for role in (CAP, LOGIN):
        for table, columns in READ.items():
            for column in columns:
                result["columns"].append({"role": role, "schema": SCHEMA, "table": table, "column": column,
                    "select": role == CAP, "insert": role == CAP and column in WRITE.get(table, []),
                    "update": role == CAP and column in CHANGE.get(table, []), "references": False})
    result["schemas"] = [{"role": role, "schema": schema, "usage": role == CAP and schema == SCHEMA,
        "create": False} for role in (CAP, LOGIN) for schema in (SCHEMA, "public")]
    result["functions"] = [{"role": role, "schema": SCHEMA, "function": function, "arguments": "",
        "owner": OWNER, "security_definer": False, "return_type": "uuid" if function == "app_tenant_id" else "trigger",
        "language": "sql" if function == "app_tenant_id" else "plpgsql", "volatility": "s" if function == "app_tenant_id" else "v",
        "execute": role == CAP and function == "app_tenant_id"} for role in (CAP, LOGIN) for function in (
            "app_tenant_id", "guard_owner_codex_append_only", "guard_owner_codex_task", "guard_owner_codex_event", "guard_owner_codex_event_presence")]
    result["databases"] = [{"role": role, "database": database, "connect": role == LOGIN and database == DATABASE,
        "create": False, "temporary": False} for role in (CAP, LOGIN) for database in (DATABASE, "postgres", "ibkr_staging")]
    result["task_relations"] = [{"table": table, "owner": OWNER, "rls": True, "force_rls": True}
        for table in ("owner_codex_sessions", "owner_codex_tasks", "owner_codex_task_events")]
    result["relation_acl"] = [{"table": table, "grantee": OWNER, "grantor": OWNER, "privilege": privilege, "grantable": False}
        for table in READ for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")]
    result["column_acl"] = [{"table": table, "column": column, "grantee": CAP, "grantor": OWNER, "privilege": operation, "grantable": False}
        for operation, tables in (("SELECT", READ), ("INSERT", WRITE), ("UPDATE", CHANGE)) for table, columns in tables.items() for column in columns]
    result["schema_acl"] = [{"grantee": grantee, "grantor": OWNER, "privilege": privilege, "grantable": False}
        for grantee, privilege in ((OWNER, "USAGE"), (OWNER, "CREATE"), (CAP, "USAGE"))]
    result["function_acl"] = [{"function": function, "arguments": "", "grantee": OWNER, "grantor": OWNER, "privilege": "EXECUTE", "grantable": False}
        for function in ("app_tenant_id", "guard_owner_codex_append_only", "guard_owner_codex_task", "guard_owner_codex_event", "guard_owner_codex_event_presence")]
    result["function_acl"].append(dict(result["function_acl"][0], grantee=CAP))
    result["database_acl"] = [{"grantee": grantee, "grantor": OWNER, "privilege": privilege, "grantable": False}
        for grantee, privilege in ((OWNER, "CONNECT"), (OWNER, "CREATE"), (OWNER, "TEMPORARY"), (LOGIN, "CONNECT"), (INSTALLER, "CONNECT"))]
    for key in ("owned_objects", "broad_relations", "sequences"):
        result[key] = []
    return result


def validate(value, target, **kwargs):
    return permissions.validate_catalog(value, target, installer_identity=INSTALLER, **kwargs)


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.target = permissions.PermissionTarget()

    def deny(self, snapshot, **kwargs):
        with self.assertRaises(permissions.PermissionDenied):
            validate(snapshot, self.target, **kwargs)

    def test_exact_target_and_identifier_injections_rejected(self):
        self.assertEqual(self.target.database, DATABASE)
        for field in ("database", "schema", "schema_owner", "capability", "login"):
            for value in ("public", 'x"; GRANT ALL TO PUBLIC;--', "", None, 1):
                with self.subTest(field=field, value=value), self.assertRaises(permissions.PermissionDenied):
                    permissions.PermissionTarget(**{field: value})
        with self.assertRaises(permissions.PermissionDenied):
            permissions.grant_statements({"capability": CAP})

    def test_target_cannot_be_mutated_and_bypassed_frozen_mutation_is_revalidated(self):
        with self.assertRaises(Exception):
            self.target.capability = "other"
        object.__setattr__(self.target, "capability", "other")
        with self.assertRaises(permissions.PermissionDenied):
            permissions.grant_statements(self.target)

    def test_exact_typed_composable_grant_manifest(self):
        statements = permissions.grant_statements(self.target)
        self.assertEqual(len(statements), 11)
        self.assertTrue(all(isinstance(statement, sql.Composable) for statement in statements))
        rendered = [statement.as_string() for statement in statements]
        expected = [f'GRANT USAGE ON SCHEMA "{SCHEMA}" TO "{CAP}"']
        for operation, tables in (("SELECT", READ), ("INSERT", WRITE), ("UPDATE", CHANGE)):
            for table, columns in sorted(tables.items()):
                quoted = ", ".join('"' + column + '"' for column in columns)
                expected.append(f'GRANT {operation} ({quoted}) ON TABLE "{SCHEMA}"."{table}" TO "{CAP}"')
        expected.append(f'GRANT EXECUTE ON FUNCTION "{SCHEMA}".app_tenant_id() TO "{CAP}"')
        self.assertEqual(rendered, expected)
        self.assertNotRegex("\n".join(rendered), "ALL|PUBLIC|GRANT OPTION|CONNECT|CREATE ROLE|PASSWORD|REVOKE|tenants")

    def test_permission_matrices_cannot_be_modified(self):
        with self.assertRaises(TypeError):
            permissions.SELECT["source"] = ("raw",)

    def test_complete_service_metadata_columns_and_fixed_manifest(self):
        result = validate(catalog(), self.target)
        self.assertEqual(result["column_privileges_verified"], 84)
        self.assertRegex(result["permission_manifest_sha256"], "^[0-9a-f]{64}$")
        self.assertIs(result["fresh_login_verified"], True)
        self.assertIs(result["metadata_acl_verified"], True)
        self.assertIs(result["end_user_authentication_verified"], False)
        self.assertIs(result["protected_receipts_verified"], False)
        self.assertIs(result["runtime_activated"], False)
        self.assertEqual(result["grants_executed"], 0)

    def test_pre_login_named_catalog_audit_does_not_claim_fresh_login(self):
        value = catalog(login_enabled=False)
        value["identity"].update(authenticated_user="TRUSTED_ADMIN", current_user="TRUSTED_ADMIN", session_user="TRUSTED_ADMIN", search_path="public")
        result = validate(value, self.target, require_fresh_login=False, login_enabled=False)
        self.assertIs(result["fresh_login_verified"], False)
        self.deny(value)

    def test_wrong_host_database_authenticated_user_role_and_path_denied(self):
        for field in ("host", "database", "authenticated_user", "session_user", "current_user", "search_path"):
            value = catalog()
            value["identity"][field] = "UNREGISTERED"
            self.deny(value)

    def test_admin_authenticated_set_session_authorization_cannot_claim_fresh_login(self):
        value = catalog()
        value["identity"]["authenticated_user"] = "TRUSTED_ADMIN"
        self.deny(value)

    def test_every_role_attribute_elevation_boolean_alias_and_connection_limit_denied(self):
        for role_index in range(3):
            for flag in ("rolinherit", "rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls"):
                for elevated in (True, 0):
                    value = catalog()
                    value["roles"][role_index][flag] = elevated
                    self.deny(value)
        for flag in ("rolcanlogin", "rolconnlimit"):
            value = catalog()
            value["roles"][0][flag] = True
            self.deny(value)

    def test_extra_missing_and_duplicate_role_denied(self):
        for change in (lambda rows: rows.pop(), lambda rows: rows.append(copy.deepcopy(rows[0])),
                       lambda rows: rows[0].update(role="unregistered")):
            value = catalog()
            change(value["roles"])
            self.deny(value)

    def test_operational_membership_cannot_admin_inherit_or_escalate(self):
        for flag in ("admin_option", "inherit_option"):
            value = catalog()
            value["memberships"][0][flag] = True
            self.deny(value)
        for role in ("pg_read_all_data", "pg_write_all_data", OWNER):
            value = catalog()
            value["memberships"][0]["role"] = role
            self.deny(value)
        value = catalog()
        value["memberships"].append(dict(value["memberships"][0], member=CAP, role=OWNER))
        self.deny(value)

    def test_creator_lifecycle_grant_only_admin_non_executable(self):
        value = catalog()
        validate(value, self.target)
        for field in ("set_option", "inherit_option"):
            changed = copy.deepcopy(value)
            changed["memberships"][-1][field] = True
            self.deny(changed)

    def test_unregistered_capability_or_login_recipient_denied(self):
        for role in (CAP, LOGIN):
            value = catalog()
            value["memberships"].append({"member": "UNREGISTERED", "role": role, "admin_option": False,
                "inherit_option": False, "set_option": True, "grantor": INSTALLER})
            self.deny(value)

    def test_role_ownership_table_broad_sequence_and_acl_grant_options_denied(self):
        for field in ("owned_objects", "broad_relations", "sequences", "relation_acl", "column_acl",
                      "schema_acl", "function_acl", "database_acl"):
            value = catalog()
            value[field].append({"synthetic": "unintended privilege"})
            self.deny(value)

    def test_no_source_tenants_credentials_or_login_column_rights(self):
        for table, column in (("tenants", "tenant_id"), ("raw_reports", "file_path"), ("nav_snapshots", "total_text"),
                              ("ibkr_flex_credentials", "key_vault_secret_name")):
            value = catalog()
            value["columns"].append({"role": CAP, "schema": SCHEMA, "table": table, "column": column,
                "select": True, "insert": False, "update": False, "references": False})
            self.deny(value)
        value = catalog()
        next(row for row in value["columns"] if row["role"] == LOGIN)["select"] = True
        self.deny(value)

    def test_no_mutation_of_authority_worker_history_or_immutable_task_columns(self):
        for table, column in (("owner_task_authority_fixture", "active"), ("owner_task_worker_fixture", "enabled"),
                              ("owner_codex_sessions", "created_at"), ("owner_codex_task_events", "code"),
                              ("owner_codex_tasks", "input_sha256")):
            value = catalog()
            next(row for row in value["columns"] if row["role"] == CAP and row["table"] == table and row["column"] == column)["update"] = True
            self.deny(value)

    def test_missing_required_read_write_privilege_or_references_denied(self):
        for operation in ("select", "insert", "update"):
            value = catalog()
            next(row for row in value["columns"] if row[operation])[operation] = False
            self.deny(value)
        value = catalog()
        value["columns"][0]["references"] = True
        self.deny(value)

    def test_schema_create_temp_and_cross_database_public_connect_denied(self):
        value = catalog()
        value["schemas"][0]["create"] = True
        self.deny(value)
        value = catalog()
        value["schemas"][1]["usage"] = True
        self.deny(value)
        for field in ("create", "temporary"):
            value = catalog()
            value["databases"][0][field] = True
            self.deny(value)
        value = catalog(login_enabled=False)
        next(r for r in value["databases"] if r["role"] == LOGIN and r["database"] == "ibkr_staging")["connect"] = True
        self.deny(value, require_fresh_login=False, login_enabled=False)

    def test_only_exact_invoker_helper_is_executable(self):
        for field, wrong in (("security_definer", True), ("return_type", "text"), ("arguments", "text"),
                              ("owner", CAP), ("language", "plpgsql"), ("volatility", "v")):
            value = catalog()
            value["functions"][0][field] = wrong
            self.deny(value)
        value = catalog()
        value["functions"][1]["execute"] = True
        self.deny(value)

    def test_metadata_force_rls_and_separate_owner_required(self):
        for field, wrong in (("rls", False), ("force_rls", False), ("owner", CAP), ("force_rls", 1)):
            value = catalog()
            value["task_relations"][0][field] = wrong
            self.deny(value)

    def test_mode_arguments_are_booleans_not_truthiness(self):
        for option in ("require_fresh_login", "login_enabled"):
            for value in (0, 1, "true", None):
                self.deny(catalog(), **{option: value})

    def test_snapshot_keys_and_types_fail_closed_with_fixed_errors(self):
        for wrong in (None, [], "credentials must not be reflected", True):
            self.deny(wrong)
        for key in catalog():
            value = catalog()
            value.pop(key)
            self.deny(value)
        value = catalog()
        value["unregistered"] = "not accepted"
        self.deny(value)

    def test_every_complete_catalog_row_rejects_unknown_missing_or_non_mapping_fields(self):
        for group in ("roles", "memberships", "columns", "relation_acl", "column_acl", "schemas", "schema_acl",
                      "functions", "function_acl", "databases", "database_acl", "task_relations"):
            for mutate in (lambda rows: rows[0].pop(next(iter(rows[0]))), lambda rows: rows[0].update(unregistered=False),
                           lambda rows: rows.__setitem__(0, None), lambda rows: rows.append(copy.deepcopy(rows[0]))):
                value = catalog()
                mutate(value[group])
                self.deny(value)
            value = catalog()
            value[group] = tuple(value[group])
            self.deny(value)

    def test_false_privilege_rows_cannot_hide_unknown_roles_or_incomplete_pairs(self):
        for group in ("columns", "schemas", "functions", "databases"):
            value = catalog()
            value[group][0]["role"] = "unregistered"
            self.deny(value)
            value = catalog()
            value[group].pop(next(i for i, row in enumerate(value[group]) if row["role"] == LOGIN))
            self.deny(value)

    def test_exact_acl_grantor_grantee_and_grant_options_are_bound(self):
        for group in ("relation_acl", "column_acl", "schema_acl", "function_acl", "database_acl"):
            for key, wrong in (("grantor", INSTALLER), ("grantee", "unregistered"), ("grantable", True), ("grantable", 0)):
                value = catalog()
                value[group][0][key] = wrong
                self.deny(value)
            value = catalog()
            value[group].pop()
            self.deny(value)

    def test_installer_parameter_is_required_exact_and_safe(self):
        with self.assertRaises(TypeError):
            permissions.validate_catalog(catalog(), self.target)
        for wrong in (None, 1, LOGIN, OWNER, CAP, "", "untrusted; RESET ROLE", "different_installer"):
            with self.assertRaises(permissions.PermissionDenied):
                permissions.validate_catalog(catalog(), self.target, installer_identity=wrong)
        value = catalog()
        for row in value["memberships"]:
            for key in ("member", "grantor"):
                if row[key] == INSTALLER:
                    row[key] = "SYNTH_INSTALLER"
        value["installer"]["role"] = "SYNTH_INSTALLER"
        for row in value["database_acl"]:
            if row["grantee"] == INSTALLER:
                row["grantee"] = "SYNTH_INSTALLER"
        permissions.validate_catalog(value, self.target, installer_identity="SYNTH_INSTALLER")

    def test_every_membership_including_owner_incoming_is_registered(self):
        for index in range(5):
            for key, wrong in (("member", "unregistered"), ("grantor", "unregistered"), ("admin_option", 0)):
                value = catalog()
                value["memberships"][index][key] = wrong
                self.deny(value)
        value = catalog()
        value["memberships"].append(dict(value["memberships"][1], member="unregistered"))
        self.deny(value)
        value = catalog()
        value["memberships"][-1]["grantor"] = INSTALLER
        self.deny(value)

    def test_superuser_installer_has_no_fabricated_pg16_creator_rows(self):
        value = catalog()
        value["installer"]["superuser"] = True
        self.deny(value)
        value["memberships"] = value["memberships"][:2]
        validate(value, self.target)

    def test_all_guard_functions_are_exact_invoker_metadata(self):
        for key, wrong in (("security_definer", True), ("owner", CAP), ("return_type", "text"),
                           ("language", "sql"), ("volatility", "s")):
            value = catalog()
            value["functions"][1][key] = wrong
            self.deny(value)
        value = catalog()
        value["functions"] = [row for row in value["functions"] if row["function"] != "guard_owner_codex_event"]
        self.deny(value)

    def test_catalog_collector_is_read_only_and_uses_named_effective_roles(self):
        value = catalog()
        identity = {key: val for key, val in value["identity"].items() if key not in ("host", "authenticated_user")}
        order = ("roles", "memberships", "owned_objects", "columns", "broad_relations", "sequences",
                 "relation_acl", "column_acl", "schemas", "schema_acl", "functions", "function_acl",
                 "databases", "database_acl", "task_relations")
        responses = [identity, value["installer"], {"role": "postgres"}] + [value[key] for key in order]
        calls = []
        def execute(query, args=()):
            calls.append((query, args))
            result = responses.pop(0)
            return SimpleNamespace(fetchone=lambda: result, fetchall=lambda: result)
        raw = SimpleNamespace(info=SimpleNamespace(host=value["identity"]["host"], user=LOGIN), execute=execute)
        result = permissions.verify_permissions(raw, self.target, installer_identity=INSTALLER)
        self.assertIs(result["fresh_login_verified"], True)
        self.assertEqual(responses, [])
        self.assertEqual(len(calls), 18)
        self.assertTrue(all(query.lstrip().startswith("SELECT ") for query, args in calls))
        self.assertTrue(all(";" not in query and "--" not in query for query, args in calls))
        self.assertNotRegex("\n".join(query for query, args in calls), r"\b(?:FROM|JOIN) pg_(?:roles|class|namespace|database|proc|auth_members)\b")
        self.assertIn("pg_catalog.has_column_privilege", "\n".join(query for query, args in calls))
        self.assertEqual(calls[1][1], (INSTALLER,))
        self.assertIn(([CAP, LOGIN],), [args for query, args in calls])

    def test_catalog_collector_missing_identity_has_fixed_error(self):
        for row in (None, {}, [], {"database": DATABASE}, {"database": DATABASE, "current_user": CAP,
                "session_user": LOGIN, "search_path": SCHEMA + ", pg_catalog", "extra": "not reflected"}):
            raw = SimpleNamespace(execute=lambda *args: SimpleNamespace(fetchone=lambda: row))
            with self.assertRaises(permissions.PermissionDenied) as raised:
                permissions.verify_permissions(raw, self.target, installer_identity=INSTALLER)
            self.assertEqual(str(raised.exception), "catalog_row_shape_mismatch")

    def test_collector_rejects_invalid_mode_before_reading_any_catalog(self):
        raw = SimpleNamespace(execute=lambda *args: self.fail("no SQL allowed for invalid mode"))
        for option in ("require_fresh_login", "login_enabled"):
            with self.assertRaises(permissions.PermissionDenied):
                permissions.verify_permissions(raw, self.target, installer_identity=INSTALLER, **{option: "true"})

    def test_source_is_catalog_only_and_has_no_provisioner_or_raw_source_queries(self):
        source = Path(permissions.__file__).read_text(encoding="utf-8")
        self.assertNotIn("psycopg.connect", source)
        self.assertNotIn("os.environ", source)
        self.assertNotIn("CREATE ROLE", source)
        self.assertNotIn("ALTER ROLE", source)
        self.assertNotIn("RESET ROLE", source)
        self.assertNotRegex(source, r"FROM\s+(?:raw_reports|nav_snapshots|ibkr_flex_credentials)\b")
        self.assertIn("has_database_privilege", source)
        self.assertIn("has_column_privilege", source)
        self.assertIn("a.is_grantable", source)


if __name__ == "__main__":
    unittest.main()
