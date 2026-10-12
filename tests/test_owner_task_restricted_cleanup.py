"""Synthetic local cleanup control tests: no PostgreSQL, network or cloud."""
import copy
from contextlib import contextmanager
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from psycopg import sql


PATH = Path(__file__).parents[1] / "scripts" / "cleanup_owner_task_restricted_trial.py"
spec = importlib.util.spec_from_file_location("restricted_cleanup_test_module", PATH)
cleanup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(cleanup)
HOST = "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"
DB = "owner_codex_restricted_20261004"
SCHEMA = "owner_codex_059_restricted"
OWNER = "ibkr_owner_codex_schema_20261004"
CAP = "ibkr_owner_codex_dml_20261004"
LOGIN = "ibkr_owner_codex_login_20261004"
INSTALLER = "SYNTH_INSTALLER"
MARKER = "wealthryse-owner-task-restricted-059-20261004-v1"
ROLE_OIDS = {OWNER: 101, CAP: 102, LOGIN: 103}


def manifest():
    return dict(mode="complete", host=HOST, database=DB, schema=SCHEMA, schema_owner=OWNER,
        capability=CAP, login=LOGIN, marker=MARKER, installer_role=INSTALLER, database_oid=100,
        migration_sha256="b4a78f64969d328f6b3ce1ce71b73cd3c033606fc8580ed1a933dd47857e9861",
        role_oids=dict(ROLE_OIDS))


def inventory():
    tables = ("tenants", "owner_task_authority_fixture", "owner_task_worker_fixture",
        "owner_codex_sessions", "owner_codex_tasks", "owner_codex_task_events")
    schemas = [dict(oid=200, name=SCHEMA, owner=OWNER, marker=MARKER),
        dict(oid=201, name="public", owner="azure_pg_admin", marker=None)]
    relations = [dict(oid=300+i, schema=SCHEMA, name=name, kind="r", owner=OWNER,
        index_table=None, primary=None, unique=None, columns=[]) for i, name in enumerate(tables)]
    indexes = [
        ("tenants_pkey", "tenants", True, True, ["tenant_id"]),
        ("owner_task_authority_fixture_pkey", "owner_task_authority_fixture", True, True, ["tenant_id", "owner_object_id"]),
        ("owner_task_worker_fixture_pkey", "owner_task_worker_fixture", True, True, ["tenant_id", "worker_id"]),
        ("owner_codex_sessions_pkey", "owner_codex_sessions", True, True, ["tenant_id", "owner_object_id", "session_id"]),
        ("owner_codex_sessions_unique_key", "owner_codex_sessions", False, True, ["tenant_id", "owner_object_id", "idempotency_key"]),
        ("owner_codex_tasks_pkey", "owner_codex_tasks", True, True, ["tenant_id", "owner_object_id", "task_id"]),
        ("owner_codex_tasks_unique_key", "owner_codex_tasks", False, True, ["tenant_id", "owner_object_id", "idempotency_key"]),
        ("owner_codex_task_events_pkey", "owner_codex_task_events", True, True, ["tenant_id", "owner_object_id", "task_id", "event_no"]),
        ("owner_codex_tasks_owner_history", "owner_codex_tasks", False, False, ["tenant_id", "owner_object_id", "session_id", "created_at", "task_id"]),
        ("owner_codex_tasks_leases", "owner_codex_tasks", False, False, ["tenant_id", "owner_object_id", "lease_expires_at"]),
    ]
    relations.extend(dict(oid=310+i, schema=SCHEMA, name=name, kind="i", owner=OWNER,
        index_table=table, primary=primary, unique=unique, columns=columns) for i, (name, table, primary, unique, columns) in enumerate(indexes))
    functions = [dict(oid=400+i, schema=SCHEMA, name=name, owner=OWNER, arguments=0, kind="f", definer=False,
        return_type="uuid" if name == "app_tenant_id" else "trigger") for i, name in enumerate(("app_tenant_id",
            "guard_owner_codex_append_only", "guard_owner_codex_task", "guard_owner_codex_event", "guard_owner_codex_event_presence"))]
    types = [dict(oid=500+i, schema=SCHEMA, name=table, owner=OWNER, kind="c", relation=table, element_relation=None)
        for i, table in enumerate(tables)]
    types.extend(dict(oid=510+i, schema=SCHEMA, name="_"+table, owner=OWNER, kind="b", relation=None, element_relation=table)
        for i, table in enumerate(tables))
    return schemas, relations, functions, types


class Fixture:
    def __init__(self):
        self.db_exists = True
        self.role_rows = [dict(oid=ROLE_OIDS[name], name=name, marker=MARKER, rolcanlogin=name == LOGIN,
            rolinherit=False, rolsuper=False, rolcreatedb=False, rolcreaterole=False,
            rolreplication=False, rolbypassrls=False, rolconnlimit=4 if name == LOGIN else -1) for name in (OWNER, CAP, LOGIN)]
        self.members = [dict(oid=600, member=LOGIN, role=CAP, grantor=INSTALLER, admin_option=False, inherit_option=False, set_option=True),
            dict(oid=601, member=INSTALLER, role=OWNER, grantor=INSTALLER, admin_option=False, inherit_option=False, set_option=True)]
        self.members.extend(dict(oid=602+i, member=INSTALLER, role=name, grantor="postgres", admin_option=True,
            inherit_option=False, set_option=False) for i, name in enumerate((OWNER, CAP, LOGIN)))
        self.inventory = inventory()
        self.dependencies = []
        self.active_sessions = 0
        self.extra = 0
        self.tenant_acl = self.tenant_column_acl = 0
        self.calls = []
        self.closed = []
        self.failure = None
        self.hook_after_drop = None
        self.admin = Fake(self, "postgres", 1001)
        self.inspector = Fake(self, DB, 1002)
        self.readback = Fake(self, "postgres", 1003)

    def run(self, **kwargs):
        options = dict(installer_identity=INSTALLER, reviewed_scope_manifest=manifest(), connect_readback=lambda:self.readback)
        options.update(kwargs)
        with patch("ibkr_etl.owner_codex_permissions.verify_permissions", return_value={"status":"passed"}):
            return cleanup.cleanup_trial(self.admin, lambda:self.inspector, **options)


class Fake:
    def __init__(self, fixture, database, pid):
        self.fixture = fixture
        self.autocommit = True
        self.info = SimpleNamespace(host=HOST, dbname=database, user=INSTALLER, transaction_status=0, backend_pid=pid)
        self.pgconn = SimpleNamespace(ssl_in_use=True)
        self.current = INSTALLER
        self.path = "pg_catalog"
        self.temporary_schema = 0

    def close(self):
        self.fixture.closed.append(self.info.backend_pid)

    @contextmanager
    def transaction(self):
        before = copy.deepcopy(self.fixture.role_rows)
        try:
            yield
            if self.fixture.failure == "role_commit":
                raise RuntimeError("SENSITIVE provider commit detail")
        except Exception:
            self.fixture.role_rows = before
            raise

    def execute(self, statement, parameters=()):
        text = statement.as_string() if isinstance(statement, sql.Composable) else statement
        f = self.fixture
        f.calls.append((self.info.dbname, text, parameters))
        if text == "SELECT pg_catalog.current_setting('search_path') AS search_path,pg_catalog.pg_my_temp_schema() AS temporary_schema":
            value = {"search_path": self.path, "temporary_schema": self.temporary_schema}
        elif text.startswith("SELECT pg_catalog.current_database"):
            value = dict(database=self.info.dbname, role=self.current, login=INSTALLER, version="160012", backend_pid=self.info.backend_pid)
        elif text.startswith("SELECT oid::bigint AS oid,datname"):
            value = dict(oid=100, name=DB, owner=OWNER, marker=MARKER) if f.db_exists else None
        elif text.startswith("SELECT oid::bigint AS oid,rolname"):
            value = copy.deepcopy(f.role_rows)
        elif text.startswith("SELECT oid::bigint AS oid,nspname"):
            value = copy.deepcopy(f.inventory[0])
        elif text.startswith("SELECT c.oid::bigint"):
            value = copy.deepcopy(f.inventory[1])
        elif text.startswith("SELECT p.oid::bigint"):
            value = copy.deepcopy(f.inventory[2])
        elif text.startswith("SELECT t.oid::bigint"):
            value = copy.deepcopy(f.inventory[3])
        elif text.startswith("SELECT (SELECT count(*)"):
            value = {"n": f.extra}
        elif "c.relname='tenants'" in text:
            value = {"n": f.tenant_column_acl if "aclexplode(col.attacl)" in text else f.tenant_acl}
        elif text.startswith("SELECT r.rolsuper"):
            value = dict(superuser=False, bootstrap="postgres")
        elif text.startswith("SELECT m.oid::bigint"):
            value = copy.deepcopy(f.members)
        elif text.startswith("SELECT d.dbid::bigint"):
            value = copy.deepcopy(f.dependencies)
        elif "pg_catalog.pg_stat_activity" in text:
            value = {"n": f.active_sessions}
        elif text.startswith("SET ROLE "):
            self.current = OWNER
            value = None
        elif text == "RESET ROLE":
            self.current = INSTALLER
            value = None
        elif text.startswith("DROP DATABASE "):
            if f.failure == "drop_before_ack":
                raise RuntimeError("SENSITIVE provider URL password")
            f.db_exists = False
            if f.hook_after_drop:
                f.hook_after_drop()
            if f.failure == "drop_after_ack":
                raise RuntimeError("SENSITIVE provider uncertainty")
            value = None
        elif text.startswith("DROP ROLE "):
            if f.failure == "role_drop":
                raise RuntimeError("SENSITIVE provider SQL")
            f.role_rows = []
            value = None
        else:
            raise AssertionError("unregistered synthetic query: "+text)
        return SimpleNamespace(fetchone=lambda:value, fetchall=lambda:value)


class CleanupTests(unittest.TestCase):
    def test_valid_complete_cleanup_and_fresh_absence_readback(self):
        f = Fixture()
        result = f.run()
        self.assertEqual(result["status"], "passed")
        self.assertIs(result["absence_verified"], True)
        self.assertIs(result["fresh_readback_verified"], True)
        self.assertIsNone(result["reason"])
        self.assertFalse(f.db_exists)
        self.assertEqual(f.role_rows, [])
        self.assertEqual(f.closed, [1002, 1003])
        self.assertEqual([text for db, text, args in f.calls if text.startswith("DROP")],
            ['DROP DATABASE "owner_codex_restricted_20261004"',
             'DROP ROLE "ibkr_owner_codex_login_20261004", "ibkr_owner_codex_dml_20261004", "ibkr_owner_codex_schema_20261004"'])
        relation_sql = next(text for db, text, args in f.calls if text.startswith("SELECT c.oid::bigint"))
        self.assertIn("FROM pg_catalog.unnest(i.indkey)", relation_sql)
        self.assertNotRegex(relation_sql, r"(?<![\w.])unnest\(")

    def assert_refused_without_drop(self, f, **kwargs):
        result = f.run(**kwargs)
        self.assertEqual(result["status"], "refused")
        self.assertIs(result["database_drop_attempted"], False)
        self.assertFalse(any(text.startswith("DROP") for db, text, args in f.calls))
        return result

    def test_partial_absent_or_roles_only_scopes_are_refused(self):
        for change in (lambda f:setattr(f,"db_exists",False), lambda f:f.role_rows.pop(), lambda f:f.inventory.__getitem__(0).pop()):
            f = Fixture(); change(f); self.assert_refused_without_drop(f)
        value = manifest(); value["mode"] = "roles_only"
        self.assert_refused_without_drop(Fixture(), reviewed_scope_manifest=value)

    def test_manifest_exact_keys_targets_and_installer_are_required(self):
        for key in manifest():
            value = manifest(); value.pop(key)
            self.assert_refused_without_drop(Fixture(), reviewed_scope_manifest=value)
        for key in ("host","database","schema","schema_owner","capability","login","marker","migration_sha256","installer_role"):
            value = manifest(); value[key] = "unregistered"
            self.assert_refused_without_drop(Fixture(), reviewed_scope_manifest=value)
        for identity in (None, LOGIN, OWNER, CAP, "untrusted;DROP DATABASE postgres"):
            self.assert_refused_without_drop(Fixture(), installer_identity=identity)

    def test_oid_bindings_are_strict_and_replacement_is_rejected(self):
        for wrong in (False, 0, -1, 4294967296, "100", None):
            value = manifest(); value["database_oid"] = wrong
            self.assert_refused_without_drop(Fixture(), reviewed_scope_manifest=value)
            value = manifest(); value["role_oids"][CAP] = wrong
            self.assert_refused_without_drop(Fixture(), reviewed_scope_manifest=value)
        value = manifest(); value["database_oid"] = 999
        self.assert_refused_without_drop(Fixture(), reviewed_scope_manifest=value)
        value = manifest(); value["role_oids"][CAP] = ROLE_OIDS[OWNER]
        self.assert_refused_without_drop(Fixture(), reviewed_scope_manifest=value)

    def test_trusted_connection_host_tls_login_role_and_pid_are_checked(self):
        for obj, key, wrong in (("info","host","unregistered"),("info","dbname","ibkr_staging"),("info","user",LOGIN),
                               ("info","transaction_status",2),("pgconn","ssl_in_use",False)):
            f = Fixture(); setattr(getattr(f.admin,obj),key,wrong); self.assert_refused_without_drop(f)
        f = Fixture(); f.admin.current = OWNER; self.assert_refused_without_drop(f)
        f = Fixture(); f.inspector.info.backend_pid = 1001; self.assert_refused_without_drop(f)

    def test_catalog_only_search_path_is_checked_before_typed_catalog_sql(self):
        for path in (None, "public", "public, pg_catalog", "pg_catalog, public", '"$user", public', [], True):
            f = Fixture(); f.admin.path = path
            result = self.assert_refused_without_drop(f)
            self.assertEqual(result["reason"], "trusted_catalog_path_required")
            self.assertEqual(f.calls, [("postgres", "SELECT pg_catalog.current_setting('search_path') AS search_path,pg_catalog.pg_my_temp_schema() AS temporary_schema", ())])
        f = Fixture(); f.inspector.path = "public, pg_catalog"
        self.assert_refused_without_drop(f)
        self.assertEqual([text for db, text, args in f.calls if db == DB],
            ["SELECT pg_catalog.current_setting('search_path') AS search_path,pg_catalog.pg_my_temp_schema() AS temporary_schema"])
        f = Fixture(); f.readback.path = "public, pg_catalog"
        result = f.run()
        self.assertEqual(result["status"], "partial_or_uncertain")
        self.assertEqual(result["reason"], "trusted_catalog_path_required")
        self.assertIs(result["role_drop_confirmed"], True)

    def test_implicit_temporary_schema_is_rejected_before_type_resolution(self):
        first = "SELECT pg_catalog.current_setting('search_path') AS search_path,pg_catalog.pg_my_temp_schema() AS temporary_schema"
        for temporary in (1, True, False, None, "0", -1, []):
            f = Fixture(); f.admin.temporary_schema = temporary
            result = self.assert_refused_without_drop(f)
            self.assertEqual(result["reason"], "trusted_catalog_path_required")
            self.assertEqual(f.calls, [("postgres", first, ())])
            f = Fixture(); f.inspector.temporary_schema = temporary
            self.assert_refused_without_drop(f)
            self.assertEqual([text for db, text, args in f.calls if db == DB], [first])
            f = Fixture(); f.readback.temporary_schema = temporary
            result = f.run()
            self.assertEqual(result["status"], "partial_or_uncertain")
            self.assertIs(result["role_drop_confirmed"], True)
            self.assertIs(result["fresh_readback_verified"], False)
            self.assertEqual(f.calls[-1], ("postgres", first, ()))

    def test_role_marker_privileged_flags_and_limit_mismatch_refused(self):
        for key, wrong in (("marker","other"),("rolsuper",True),("rolinherit",True),("rolbypassrls",1),("rolconnlimit",4)):
            f = Fixture(); f.role_rows[0][key] = wrong; self.assert_refused_without_drop(f)

    def test_active_sessions_block_without_forced_termination(self):
        for n in (1, True, "0"):
            f = Fixture(); f.active_sessions = n; self.assert_refused_without_drop(f)
        f = Fixture(); self.assertEqual(f.run()["status"],"passed")
        first_session_check = next(i for i,row in enumerate(f.calls) if "pg_stat_activity" in row[1])
        self.assertTrue(all(db == "postgres" for db,text,args in f.calls[first_session_check:] if text.startswith("DROP")))

    def test_inventory_unknown_schema_table_index_function_type_or_owner_refused(self):
        for group, field, wrong in ((0,"name","unknown"),(0,"owner",CAP),(1,"name","raw_source_table"),
                                   (1,"owner",INSTALLER),(1,"kind","S"),(2,"definer",True),(3,"owner",CAP)):
            f = Fixture(); f.inventory[group][0][field] = wrong; self.assert_refused_without_drop(f)
        f = Fixture(); f.inventory[1][-1]["columns"] = ["unexpected"]
        self.assert_refused_without_drop(f)
        f = Fixture(); f.extra = 1; self.assert_refused_without_drop(f)

    def test_duplicate_oids_and_malformed_inventory_rows_are_fixed_denials(self):
        for i in range(4):
            vectors = inventory(); vectors[i][0]["oid"] = vectors[i][1]["oid"]
            with self.assertRaises(cleanup.CleanupError): cleanup.validate_inventory(*vectors)
            vectors = inventory(); vectors[i][0].pop("oid")
            with self.assertRaises(cleanup.CleanupError): cleanup.validate_inventory(*vectors)

    def test_unknown_acl_recipients_and_permission_verifier_errors_block(self):
        for field in ("tenant_acl","tenant_column_acl"):
            f = Fixture(); setattr(f,field,1); self.assert_refused_without_drop(f)
        f = Fixture()
        with patch("ibkr_etl.owner_codex_permissions.verify_permissions", side_effect=RuntimeError("SENSITIVE")):
            result = cleanup.cleanup_trial(f.admin, lambda:f.inspector, installer_identity=INSTALLER,
                reviewed_scope_manifest=manifest(), connect_readback=lambda:f.readback)
        self.assertEqual(result["status"],"refused")
        self.assertNotIn("SENSITIVE",str(result))

    def test_unknown_recipients_grantors_and_executable_creator_memberships_block(self):
        for index, key, wrong in ((0,"member","unknown"),(1,"grantor","unknown"),(2,"set_option",True),(3,"inherit_option",True)):
            f = Fixture(); f.members[index][key] = wrong; self.assert_refused_without_drop(f)
        f = Fixture(); f.members.append(dict(f.members[1],oid=699,member="unknown")); self.assert_refused_without_drop(f)

    def test_cross_database_unrecognized_class_or_owner_dependencies_block(self):
        base = dict(database_oid=999, class_schema="pg_catalog", class_name="pg_class", object_oid=300, subid=0, kind="o", role=OWNER)
        for change in ({},{"database_oid":100,"class_name":"pg_default_acl"},{"database_oid":100,"object_oid":999},
                       {"database_oid":100,"role":"unknown"},{"database_oid":100,"class_schema":"public"}):
            f = Fixture(); f.dependencies=[dict(base,**change)]; self.assert_refused_without_drop(f)

    def test_valid_registered_dependencies_do_not_authorize_external_objects(self):
        f = Fixture()
        f.dependencies = [dict(database_oid=0, class_schema="pg_catalog", class_name="pg_auth_members",
            object_oid=600, subid=0, kind="a", role=CAP)]
        self.assertEqual(f.run()["status"],"passed")

    def test_unknown_drop_acknowledgement_is_not_described_as_rollback(self):
        for phase in ("drop_before_ack","drop_after_ack"):
            f = Fixture(); f.failure = phase; result = f.run()
            self.assertEqual(result["status"],"partial_or_uncertain")
            self.assertIs(result["database_drop_attempted"],True)
            self.assertIs(result["database_drop_confirmed"],False)
            self.assertIs(result["role_drop_attempted"],False)
            self.assertEqual(f.admin.current,INSTALLER)
            self.assertNotIn("SENSITIVE",str(result))

    def test_role_drop_failure_preserves_confirmed_database_loss_and_no_retry(self):
        for phase in ("role_drop","role_commit"):
            f = Fixture(); f.failure=phase; result=f.run()
            self.assertEqual(result["status"],"partial_or_uncertain")
            self.assertIs(result["database_drop_confirmed"],True)
            self.assertIs(result["role_drop_confirmed"],False)
            self.assertFalse(f.db_exists)
            self.assertEqual(len(f.role_rows),3)
            self.assertIs(result["automatic_retry"],False)

    def test_role_oid_or_membership_change_after_database_drop_stops_role_deletion(self):
        for change in (lambda f:f.role_rows[0].update(oid=999), lambda f:f.members[0].update(member="unknown")):
            f=Fixture(); f.hook_after_drop=lambda:change(f); result=f.run()
            self.assertEqual(result["status"],"partial_or_uncertain")
            self.assertIs(result["database_drop_confirmed"],True)
            self.assertIs(result["role_drop_attempted"],False)

    def test_absence_requires_distinct_fresh_readback_and_not_same_connection(self):
        for change in (lambda f:setattr(f.readback.info,"backend_pid",1001), lambda f:setattr(f.readback.info,"host","unknown")):
            f=Fixture(); change(f); result=f.run()
            self.assertEqual(result["status"],"partial_or_uncertain")
            self.assertIs(result["role_drop_confirmed"],True)
            self.assertIs(result["absence_verified"],False)
        f=Fixture(); result=f.run(connect_readback=lambda:f.admin)
        self.assertEqual(result["reason"],"independent_cleanup_readback_required")
        self.assertNotIn(1001,f.closed)

    def test_candidate_manifest_is_read_only_and_is_not_authorization(self):
        f=Fixture(); value=cleanup.snapshot_candidate_manifest(f.admin,installer_identity=INSTALLER)
        self.assertEqual(value,manifest())
        self.assertTrue(all(text.startswith("SELECT") for db,text,args in f.calls))
        self.assertNotIn("protected_intent_verified",value)

    def test_import_is_inert_and_has_no_network_credential_reader_or_cli(self):
        source=PATH.read_text(encoding="utf-8")
        self.assertNotIn("psycopg.connect",source)
        self.assertNotIn("os.environ",source)
        self.assertNotIn("__main__",source)
        self.assertNotIn("argparse",source)
        self.assertNotIn("pg_terminate_backend",source)
        self.assertNotIn("DROP OWNED",source)
        self.assertNotIn("DROP DATABASE IF EXISTS",source)
        self.assertNotRegex(source,r"sql\.SQL\(\"[^\"]*(?:CASCADE|FORCE)")


if __name__ == "__main__":
    unittest.main()
