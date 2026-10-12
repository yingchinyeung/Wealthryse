"""INERT, complete-scope-only cleanup for one reviewed synthetic SQL trial.

No CLI, connector, environment/credential reader, model or cloud client exists
here. Importing does nothing. A trusted external controller must verify frozen
source, protect cleanup intent and bind the reviewed OID manifest before calling
this helper. A catalog candidate is NOT authorization. All partial setup needs
separate diagnosis/review; no implicit cleanup, retry, FORCE or CASCADE is used.
DROP DATABASE is nontransactional: an error after dispatch is not a rollback.
"""
import re

from psycopg import sql


HOST = "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"
DATABASE = "owner_codex_restricted_20261004"
SCHEMA = "owner_codex_059_restricted"
OWNER = "ibkr_owner_codex_schema_20261004"
CAPABILITY = "ibkr_owner_codex_dml_20261004"
LOGIN = "ibkr_owner_codex_login_20261004"
ROLES = (OWNER, CAPABILITY, LOGIN)
MARKER = "wealthryse-owner-task-restricted-059-20261004-v1"
MIGRATION_SHA256 = "b4a78f64969d328f6b3ce1ce71b73cd3c033606fc8580ed1a933dd47857e9861"
TABLES = ("tenants", "owner_task_authority_fixture", "owner_task_worker_fixture",
    "owner_codex_sessions", "owner_codex_tasks", "owner_codex_task_events")
FUNCTIONS = ("app_tenant_id", "guard_owner_codex_append_only", "guard_owner_codex_task",
    "guard_owner_codex_event", "guard_owner_codex_event_presence")
_INDEXES = {
    ("tenants", True, True, ("tenant_id",)),
    ("owner_task_authority_fixture", True, True, ("tenant_id", "owner_object_id")),
    ("owner_task_worker_fixture", True, True, ("tenant_id", "worker_id")),
    ("owner_codex_sessions", True, True, ("tenant_id", "owner_object_id", "session_id")),
    ("owner_codex_sessions", False, True, ("tenant_id", "owner_object_id", "idempotency_key")),
    ("owner_codex_tasks", True, True, ("tenant_id", "owner_object_id", "task_id")),
    ("owner_codex_tasks", False, True, ("tenant_id", "owner_object_id", "idempotency_key")),
    ("owner_codex_task_events", True, True, ("tenant_id", "owner_object_id", "task_id", "event_no")),
    ("owner_codex_tasks", False, False, ("tenant_id", "owner_object_id", "session_id", "created_at", "task_id")),
    ("owner_codex_tasks", False, False, ("tenant_id", "owner_object_id", "lease_expires_at")),
}


class CleanupError(ValueError):
    def __init__(self, code):
        self.code = code if type(code) is str and re.fullmatch("[a-z][a-z0-9_]{0,79}", code) else "cleanup_contract_failed"
        super().__init__(self.code)


def _require(value, code):
    if not value:
        raise CleanupError(code)


def _oid(value):
    return type(value) is int and 0 < value <= 4294967295


def _installer(value):
    _require(type(value) is str and re.fullmatch("[A-Za-z_][A-Za-z0-9_]{0,62}", value)
        and value not in ROLES, "trusted_installer_identity_required")


def _row(value, keys):
    _require(type(value) is dict and set(value) == set(keys), "cleanup_catalog_shape_mismatch")


def _rows(value, keys):
    _require(type(value) is list, "cleanup_catalog_shape_mismatch")
    for row in value:
        _row(row, keys)
    return value


def validate_manifest(value, *, installer_identity):
    _installer(installer_identity)
    expected = dict(mode="complete", host=HOST, database=DATABASE, schema=SCHEMA,
        schema_owner=OWNER, capability=CAPABILITY, login=LOGIN, marker=MARKER,
        migration_sha256=MIGRATION_SHA256, installer_role=installer_identity)
    _row(value, set(expected) | {"database_oid", "role_oids"})
    _require(all(type(value[k]) is type(v) and value[k] == v for k, v in expected.items()), "unreviewed_cleanup_scope")
    _require(_oid(value["database_oid"]) and type(value["role_oids"]) is dict
        and set(value["role_oids"]) == set(ROLES) and all(_oid(v) for v in value["role_oids"].values())
        and len(set(value["role_oids"].values())) == 3, "cleanup_oid_binding_required")


def _bind(raw, database, installer_identity):
    _installer(installer_identity)
    _require(database in ("postgres", DATABASE) and raw.autocommit is True
        and raw.info.transaction_status == 0 and raw.info.host == HOST
        and raw.info.dbname == database and raw.info.user == installer_identity
        and raw.pgconn.ssl_in_use is True, "fresh_trusted_cleanup_connection_required")
    path = raw.execute("SELECT pg_catalog.current_setting('search_path') AS search_path,"
        "pg_catalog.pg_my_temp_schema() AS temporary_schema").fetchone()
    _row(path, ("search_path", "temporary_schema"))
    _require(path["search_path"] == "pg_catalog" and type(path["search_path"]) is str
        and type(path["temporary_schema"]) is int and path["temporary_schema"] == 0, "trusted_catalog_path_required")
    row = raw.execute("SELECT pg_catalog.current_database() AS database,current_user::text AS role,"
        "session_user::text AS login,pg_catalog.current_setting('server_version_num') AS version,pg_catalog.pg_backend_pid() AS backend_pid").fetchone()
    _row(row, ("database", "role", "login", "version", "backend_pid"))
    _require(row["database"] == database and row["role"] == row["login"] == installer_identity
        and type(row["version"]) is str and re.fullmatch("16[0-9]{4}", row["version"])
        and type(row["backend_pid"]) is int and 0 < row["backend_pid"] <= 2147483647
        and raw.info.backend_pid == row["backend_pid"], "cleanup_connection_identity_mismatch")
    return row["backend_pid"]


def _metadata(raw):
    database = raw.execute("SELECT oid::bigint AS oid,datname AS name,pg_catalog.pg_get_userbyid(datdba) AS owner,"
        "pg_catalog.shobj_description(oid,'pg_database') AS marker FROM pg_catalog.pg_database WHERE datname=%s", (DATABASE,)).fetchone()
    roles = raw.execute("SELECT oid::bigint AS oid,rolname AS name,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,"
        "rolreplication,rolbypassrls,rolconnlimit,pg_catalog.shobj_description(oid,'pg_authid') AS marker "
        "FROM pg_catalog.pg_roles WHERE rolname=ANY(%s) ORDER BY rolname", (list(ROLES),)).fetchall()
    return database, roles


def _registered_roles(roles):
    _rows(roles, ("oid", "name", "rolcanlogin", "rolinherit", "rolsuper", "rolcreatedb", "rolcreaterole",
        "rolreplication", "rolbypassrls", "rolconnlimit", "marker"))
    _require(len(roles) == 3 and {r["name"] for r in roles} == set(ROLES), "complete_registered_roles_required")
    for row in roles:
        _require(_oid(row["oid"]) and row["marker"] == MARKER and type(row["rolcanlogin"]) is bool
            and (row["name"] == LOGIN or row["rolcanlogin"] is False)
            and all(row[k] is False for k in ("rolinherit", "rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls"))
            and type(row["rolconnlimit"]) is int and row["rolconnlimit"] == (4 if row["name"] == LOGIN else -1), "cleanup_role_attributes_mismatch")
    return {r["name"]: r["oid"] for r in roles}, next(r["rolcanlogin"] for r in roles if r["name"] == LOGIN)


def _candidate(raw, installer_identity):
    database, roles = _metadata(raw)
    _row(database, ("oid", "name", "owner", "marker"))
    _require(_oid(database["oid"]) and database["name"] == DATABASE and database["owner"] == OWNER
        and database["marker"] == MARKER, "complete_registered_database_required")
    role_oids, enabled = _registered_roles(roles)
    manifest = dict(mode="complete", host=HOST, database=DATABASE, schema=SCHEMA, schema_owner=OWNER,
        capability=CAPABILITY, login=LOGIN, marker=MARKER, migration_sha256=MIGRATION_SHA256,
        installer_role=installer_identity, database_oid=database["oid"], role_oids=role_oids)
    validate_manifest(manifest, installer_identity=installer_identity)
    return manifest, enabled


def snapshot_candidate_manifest(admin, *, installer_identity):
    """Read-only candidate bindings; NOT protected authorization/eligibility."""
    try:
        _bind(admin, "postgres", installer_identity)
        return _candidate(admin, installer_identity)[0]
    except CleanupError:
        raise
    except Exception:
        raise CleanupError("cleanup_candidate_read_failed") from None


def _inventory(raw):
    ns = "n.nspname !~ '^pg_' AND n.nspname<>'information_schema'"
    schemas = raw.execute("SELECT oid::bigint AS oid,nspname AS name,pg_catalog.pg_get_userbyid(nspowner) AS owner,"
        "pg_catalog.obj_description(oid,'pg_namespace') AS marker FROM pg_catalog.pg_namespace n WHERE " + ns).fetchall()
    relations = raw.execute("SELECT c.oid::bigint AS oid,n.nspname AS schema,c.relname AS name,c.relkind AS kind,"
        "pg_catalog.pg_get_userbyid(c.relowner) AS owner,t.relname AS index_table,i.indisprimary AS \"primary\","
        "i.indisunique AS \"unique\",ARRAY(SELECT a.attname::text FROM pg_catalog.unnest(i.indkey) WITH ORDINALITY k(attnum,ordinal) "
        "JOIN pg_catalog.pg_attribute a ON a.attrelid=i.indrelid AND a.attnum=k.attnum WHERE k.ordinal<=i.indnkeyatts ORDER BY k.ordinal) AS columns "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_catalog.pg_index i ON i.indexrelid=c.oid LEFT JOIN pg_catalog.pg_class t ON t.oid=i.indrelid WHERE " + ns).fetchall()
    functions = raw.execute("SELECT p.oid::bigint AS oid,n.nspname AS schema,p.proname AS name,pg_catalog.pg_get_userbyid(p.proowner) AS owner,"
        "p.pronargs AS arguments,p.prokind AS kind,p.prosecdef AS definer,p.prorettype::regtype::text AS return_type "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_namespace n ON n.oid=p.pronamespace WHERE " + ns).fetchall()
    types = raw.execute("SELECT t.oid::bigint AS oid,n.nspname AS schema,t.typname AS name,pg_catalog.pg_get_userbyid(t.typowner) AS owner,"
        "t.typtype AS kind,c.relname AS relation,e.relname AS element_relation FROM pg_catalog.pg_type t "
        "JOIN pg_catalog.pg_namespace n ON n.oid=t.typnamespace LEFT JOIN pg_catalog.pg_class c ON c.oid=t.typrelid "
        "LEFT JOIN pg_catalog.pg_type el ON el.oid=t.typelem LEFT JOIN pg_catalog.pg_class e ON e.oid=el.typrelid WHERE " + ns).fetchall()
    # All are metadata inventories/counts, never source/account row reads.
    extra = raw.execute("SELECT (SELECT count(*) FROM pg_catalog.pg_largeobject_metadata)+"
        "(SELECT count(*) FROM pg_catalog.pg_event_trigger)+(SELECT count(*) FROM pg_catalog.pg_foreign_data_wrapper)+"
        "(SELECT count(*) FROM pg_catalog.pg_foreign_server)+(SELECT count(*) FROM pg_catalog.pg_publication)+"
        "(SELECT count(*) FROM pg_catalog.pg_subscription)+(SELECT count(*) FROM pg_catalog.pg_extension WHERE extname<>'plpgsql')+"
        "(SELECT count(*) FROM pg_catalog.pg_operator o JOIN pg_catalog.pg_namespace n ON n.oid=o.oprnamespace WHERE " + ns + ")+"
        "(SELECT count(*) FROM pg_catalog.pg_collation c JOIN pg_catalog.pg_namespace n ON n.oid=c.collnamespace WHERE " + ns + ")+"
        "(SELECT count(*) FROM pg_catalog.pg_conversion c JOIN pg_catalog.pg_namespace n ON n.oid=c.connamespace WHERE " + ns + ")+"
        "(SELECT count(*) FROM pg_catalog.pg_ts_config c JOIN pg_catalog.pg_namespace n ON n.oid=c.cfgnamespace WHERE " + ns + ")+"
        "(SELECT count(*) FROM pg_catalog.pg_ts_dict c JOIN pg_catalog.pg_namespace n ON n.oid=c.dictnamespace WHERE " + ns + ")+"
        "(SELECT count(*) FROM pg_catalog.pg_ts_parser c JOIN pg_catalog.pg_namespace n ON n.oid=c.prsnamespace WHERE " + ns + ")+"
        "(SELECT count(*) FROM pg_catalog.pg_ts_template c JOIN pg_catalog.pg_namespace n ON n.oid=c.tmplnamespace WHERE " + ns + ")+"
        "(SELECT count(*) FROM pg_catalog.pg_rewrite w JOIN pg_catalog.pg_class c ON c.oid=w.ev_class JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE " + ns + ") AS n").fetchone()
    _row(extra, ("n",))
    _require(type(extra["n"]) is int and extra["n"] == 0, "unknown_cleanup_objects")
    return validate_inventory(schemas, relations, functions, types)


def validate_inventory(schemas, relations, functions, types):
    for rows in (schemas, relations, functions, types):
        _require(type(rows) is list and all(type(r) is dict and _oid(r.get("oid")) for r in rows)
            and len({r["oid"] for r in rows}) == len(rows), "cleanup_object_oid_inventory_mismatch")
    _rows(schemas, ("oid", "name", "owner", "marker"))
    _require(all(type(r["name"]) is str and type(r["owner"]) is str for r in schemas)
        and len(schemas) == 2 and {r["name"] for r in schemas} == {SCHEMA, "public"}
        and all(_oid(r["oid"]) for r in schemas), "unknown_cleanup_schemas")
    for row in schemas:
        _require((row["owner"] == OWNER and row["marker"] == MARKER) if row["name"] == SCHEMA
            else (row["owner"] in ("azure_pg_admin", "pg_database_owner") and row["marker"] is None), "cleanup_schema_owner_mismatch")
    _rows(relations, ("oid", "schema", "name", "kind", "owner", "index_table", "primary", "unique", "columns"))
    _require(all(type(r["name"]) is str and type(r["index_table"]) in (str, type(None)) for r in relations)
        and len(relations) == 16 and len({r["name"] for r in relations}) == 16 and all(_oid(r["oid"])
        and r["schema"] == SCHEMA and r["owner"] == OWNER and r["kind"] in ("r", "i")
        and type(r["name"]) is str and re.fullmatch("[a-z][a-z0-9_]{0,62}", r["name"]) for r in relations), "unknown_cleanup_relations")
    tables = [r for r in relations if r["kind"] == "r"]
    _require(len(tables) == 6 and {r["name"] for r in tables} == set(TABLES) and all(r["index_table"] is None
        and r["primary"] is None and r["unique"] is None and r["columns"] == [] for r in tables), "cleanup_table_inventory_mismatch")
    indexes = [r for r in relations if r["kind"] == "i"]
    _require(all(type(r["primary"]) is bool and type(r["unique"]) is bool and type(r["columns"]) is list
        and all(type(c) is str for c in r["columns"]) for r in indexes) and len(indexes) == 10
        and {(r["index_table"], r["primary"], r["unique"], tuple(r["columns"])) for r in indexes} == _INDEXES
        and {r["name"] for r in indexes if not r["unique"]} == {"owner_codex_tasks_owner_history", "owner_codex_tasks_leases"}, "cleanup_index_inventory_mismatch")
    _rows(functions, ("oid", "schema", "name", "owner", "arguments", "kind", "definer", "return_type"))
    _require(all(type(r["name"]) is str for r in functions) and len(functions) == 5 and {r["name"] for r in functions} == set(FUNCTIONS) and all(_oid(r["oid"])
        and r["schema"] == SCHEMA and r["owner"] == OWNER and type(r["arguments"]) is int and r["arguments"] == 0
        and r["kind"] == "f" and r["definer"] is False and r["return_type"] == ("uuid" if r["name"] == "app_tenant_id" else "trigger") for r in functions), "unknown_cleanup_functions")
    _rows(types, ("oid", "schema", "name", "owner", "kind", "relation", "element_relation"))
    expected = {(table, "c", table, None) for table in TABLES} | {("_" + table, "b", None, table) for table in TABLES}
    _require(len(types) == 12 and all(_oid(r["oid"]) and r["schema"] == SCHEMA and r["owner"] == OWNER
        and all(type(r[k]) in (str, type(None)) for k in ("name", "kind", "relation", "element_relation")) for r in types)
        and {(r["name"], r["kind"], r["relation"], r["element_relation"]) for r in types} == expected, "unknown_cleanup_types")
    return {"pg_namespace": {r["oid"] for r in schemas if r["name"] == SCHEMA},
        "pg_class": {r["oid"] for r in relations}, "pg_proc": {r["oid"] for r in functions}, "pg_type": {r["oid"] for r in types}}


def _memberships(admin, manifest):
    installer = manifest["installer_role"]
    config = admin.execute("SELECT r.rolsuper AS superuser,b.rolname AS bootstrap FROM pg_catalog.pg_roles r "
        "CROSS JOIN pg_catalog.pg_roles b WHERE r.rolname=%s AND b.oid=10", (installer,)).fetchone()
    _row(config, ("superuser", "bootstrap"))
    _require(type(config["superuser"]) is bool and type(config["bootstrap"]) is str, "cleanup_installer_catalog_mismatch")
    members = admin.execute("SELECT m.oid::bigint AS oid,member.rolname AS member,parent.rolname AS role,grantor.rolname AS grantor,"
        "m.admin_option,m.inherit_option,m.set_option FROM pg_catalog.pg_auth_members m "
        "JOIN pg_catalog.pg_roles member ON member.oid=m.member JOIN pg_catalog.pg_roles parent ON parent.oid=m.roleid "
        "JOIN pg_catalog.pg_roles grantor ON grantor.oid=m.grantor WHERE roleid=ANY(%s) OR m.member=ANY(%s)",
        (list(manifest["role_oids"].values()), list(manifest["role_oids"].values()))).fetchall()
    _rows(members, ("oid", "member", "role", "grantor", "admin_option", "inherit_option", "set_option"))
    _require(all(_oid(r["oid"]) and all(type(r[k]) is str for k in ("member", "role", "grantor"))
        and all(type(r[k]) is bool for k in ("admin_option", "inherit_option", "set_option")) for r in members), "cleanup_membership_shape_mismatch")
    expected = {(LOGIN, CAPABILITY, installer, False, False, True), (installer, OWNER, installer, False, False, True)}
    if not config["superuser"]:
        expected.update((installer, role, config["bootstrap"], True, False, False) for role in ROLES)
    actual = {(r["member"], r["role"], r["grantor"], r["admin_option"], r["inherit_option"], r["set_option"]) for r in members}
    _require(len(members) == len(actual) == len(expected) and actual == expected
        and len({r["oid"] for r in members}) == len(members), "unregistered_cleanup_memberships")
    return {r["oid"] for r in members}


def _dependencies(admin, manifest, allowed, *, database_exists):
    member_ids = _memberships(admin, manifest)
    rows = admin.execute("SELECT d.dbid::bigint AS database_oid,n.nspname AS class_schema,c.relname AS class_name,"
        "d.objid::bigint AS object_oid,d.objsubid AS subid,d.deptype AS kind,pg_catalog.pg_get_userbyid(d.refobjid) AS role "
        "FROM pg_catalog.pg_shdepend d JOIN pg_catalog.pg_class c ON c.oid=d.classid JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "WHERE d.refclassid='pg_catalog.pg_authid'::regclass AND d.refobjid=ANY(%s)", (list(manifest["role_oids"].values()),)).fetchall()
    _rows(rows, ("database_oid", "class_schema", "class_name", "object_oid", "subid", "kind", "role"))
    for row in rows:
        _require(type(row["database_oid"]) is int and row["database_oid"] >= 0 and _oid(row["object_oid"])
            and type(row["subid"]) is int and row["subid"] >= 0 and row["role"] in ROLES
            and type(row["class_name"]) is str and type(row["kind"]) is str
            and row["class_schema"] == "pg_catalog", "unknown_cleanup_dependencies")
        local = database_exists and row["database_oid"] == manifest["database_oid"]
        permitted = (local and row["object_oid"] in allowed.get(row["class_name"], set())
            and row["kind"] in ("o", "a", "i") and (row["role"] == OWNER or row["kind"] == "a"))
        permitted |= (database_exists and row["database_oid"] == 0 and row["class_name"] == "pg_database"
            and row["object_oid"] == manifest["database_oid"] and row["subid"] == 0
            and ((row["role"] == OWNER and row["kind"] == "o") or (row["role"] == LOGIN and row["kind"] == "a")))
        permitted |= (row["database_oid"] == 0 and row["class_name"] == "pg_auth_members"
            and row["object_oid"] in member_ids and row["subid"] == 0 and row["kind"] == "a")
        _require(permitted, "external_or_unknown_role_dependencies")


def _no_sessions(admin):
    row = admin.execute("SELECT count(*) AS n FROM pg_catalog.pg_stat_activity WHERE datname=%s OR usename=ANY(%s)", (DATABASE, list(ROLES))).fetchone()
    _row(row, ("n",))
    _require(type(row["n"]) is int and row["n"] == 0, "active_cleanup_sessions")


def _result():
    return dict(status="refused", stage="restricted-trial-cleanup", reason="cleanup_not_verified",
        database_drop_attempted=False, database_drop_confirmed=False, role_drop_attempted=False,
        role_drop_confirmed=False, absence_verified=False, production_database_touched=False,
        fresh_readback_verified=False, ibkr_source_writes=False, existing_service_grants_changed=False, model_calls=0, automatic_retry=False)


def cleanup_trial(admin, connect_disposable, *, installer_identity, reviewed_scope_manifest, connect_readback):
    """Execute only externally protected/reviewed complete synthetic scope.

    Connections and callbacks stay within trusted control code, never models.
    Failure leaves evidence/resources for a separate review, not an auto retry.
    A separate fresh connection must establish final catalog absence.
    """
    result = _result()
    try:
        validate_manifest(reviewed_scope_manifest, installer_identity=installer_identity)
        _require(callable(connect_disposable) and callable(connect_readback), "trusted_cleanup_callbacks_required")
        admin_pid = _bind(admin, "postgres", installer_identity)
        manifest, enabled = _candidate(admin, installer_identity)
        _require(manifest == reviewed_scope_manifest, "reviewed_cleanup_oids_changed")
        raw = connect_disposable()
        _require(raw is not admin, "distinct_disposable_inspector_required")
        try:
            inspector_pid = _bind(raw, DATABASE, installer_identity)
            _require(inspector_pid != admin_pid, "distinct_disposable_inspector_required")
            _require(_candidate(raw, installer_identity)[0] == manifest, "inspected_cleanup_scope_changed")
            allowed = _inventory(raw)
            from ibkr_etl.owner_codex_permissions import PermissionTarget, verify_permissions
            verify_permissions(raw, PermissionTarget(), installer_identity=installer_identity,
                require_fresh_login=False, login_enabled=enabled)
            # Tenants is deliberately excluded from capability grants; no
            # third-party recipient may depend on even this synthetic table.
            row = raw.execute("SELECT count(*) AS n FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace,"
                "LATERAL pg_catalog.aclexplode(coalesce(c.relacl,pg_catalog.acldefault('r',c.relowner))) a "
                "WHERE n.nspname=%s AND c.relname='tenants' AND a.grantee<>c.relowner", (SCHEMA,)).fetchone()
            _require(type(row) is dict and row == {"n": 0} and type(row["n"]) is int, "unregistered_tenant_recipients")
            row = raw.execute("SELECT count(*) AS n FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
                "JOIN pg_catalog.pg_attribute col ON col.attrelid=c.oid,LATERAL pg_catalog.aclexplode(col.attacl) a "
                "WHERE n.nspname=%s AND c.relname='tenants' AND a.grantee<>c.relowner", (SCHEMA,)).fetchone()
            _require(type(row) is dict and row == {"n": 0} and type(row["n"]) is int, "unregistered_tenant_column_recipients")
            _dependencies(admin, manifest, allowed, database_exists=True)
        finally:
            raw.close()
        _require(_candidate(admin, installer_identity)[0] == manifest, "cleanup_scope_changed_before_drop")
        _no_sessions(admin)
        admin.execute(sql.SQL("SET ROLE {}").format(sql.Identifier(OWNER)))
        try:
            result["database_drop_attempted"] = True
            admin.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(DATABASE)))
            result["database_drop_confirmed"] = True
        finally:
            admin.execute("RESET ROLE")
        _bind(admin, "postgres", installer_identity)
        _no_sessions(admin)
        database, roles = _metadata(admin)
        _require(database is None and _registered_roles(roles)[0] == manifest["role_oids"], "role_scope_changed_after_database_drop")
        _dependencies(admin, manifest, {}, database_exists=False)
        result["role_drop_attempted"] = True
        with admin.transaction():
            admin.execute(sql.SQL("DROP ROLE {}, {}, {}").format(*(sql.Identifier(r) for r in (LOGIN, CAPABILITY, OWNER))))
        result["role_drop_confirmed"] = True
        readback = connect_readback()
        _require(readback is not admin and readback is not raw, "independent_cleanup_readback_required")
        try:
            readback_pid = _bind(readback, "postgres", installer_identity)
            _require(readback_pid not in (admin_pid, inspector_pid), "fresh_cleanup_readback_required")
            _require(_metadata(readback) == (None, []), "cleanup_absence_readback_failed")
        finally:
            readback.close()
        result.update(status="passed", reason=None, absence_verified=True, fresh_readback_verified=True)
    except Exception as error:
        result["status"] = "partial_or_uncertain" if result["database_drop_attempted"] or result["role_drop_attempted"] else "refused"
        result["reason"] = error.code if isinstance(error, CleanupError) else "cleanup_provider_or_contract_failed"
    return result
