"""Source-only permission specification for a disposable owner-task rehearsal.

Nothing connects, creates identities, executes grants or activates a runtime.
Custom tenant/owner GUCs are trusted-service context, NOT authentication. A
restricted SQL login must never be exposed to a model, even after acceptance:
direct DML can forge receipt hashes and select another valid GUC scope. This
module verifies metadata ACLs, not receipt protection or authenticated end-user
entitlements. The two Microsoft-owned system databases follow the explicitly approved
private-host metadata policy; real provider access tests remain mandatory.
"""
from dataclasses import dataclass
from types import MappingProxyType
import hashlib
import json
import re

from psycopg import sql
from ibkr_etl import owner_codex_isolated_target as isolated
import owner_task_azure_database_boundary as boundary


HOST = isolated.HOST
DATABASE = isolated.DATABASE
SCHEMA = isolated.SCHEMA
SCHEMA_OWNER = isolated.SCHEMA_OWNER
CAPABILITY = isolated.CAPABILITY
LOGIN = isolated.LOGIN
MARKER = isolated.MARKER
LOGIN_CONNECTION_LIMIT = 4
TASK_TABLES = ("owner_codex_sessions", "owner_codex_tasks", "owner_codex_task_events")
SELECT = {
    "owner_codex_sessions": ("tenant_id", "owner_object_id", "session_id", "idempotency_key", "created_at"),
    "owner_codex_tasks": ("tenant_id", "owner_object_id", "task_id", "session_id", "idempotency_key", "task_kind",
        "subject_ids", "input_sha256", "request_sha256", "authority_sha256", "state", "revision", "worker_id",
        "lease_token", "lease_expires_at", "cancel_requested_at", "intent_receipt_sha256", "outcome_receipt_sha256",
        "result_sha256", "error_code", "delivery_available", "created_at", "updated_at"),
    "owner_codex_task_events": ("tenant_id", "owner_object_id", "task_id", "event_no", "state", "code", "occurred_at"),
    "owner_task_authority_fixture": ("tenant_id", "owner_object_id", "active", "role_name", "registered_subject_ids", "authorized_subject_ids"),
    "owner_task_worker_fixture": ("tenant_id", "worker_id", "service_principal_id", "enabled"),
}
INSERT = {
    "owner_codex_sessions": SELECT["owner_codex_sessions"],
    "owner_codex_tasks": ("tenant_id", "owner_object_id", "task_id", "session_id", "idempotency_key", "task_kind",
        "subject_ids", "input_sha256", "request_sha256", "authority_sha256", "state", "revision",
        "delivery_available", "created_at", "updated_at"),
    "owner_codex_task_events": SELECT["owner_codex_task_events"],
}
UPDATE = {"owner_codex_tasks": ("state", "revision", "worker_id", "lease_token", "lease_expires_at", "cancel_requested_at",
    "intent_receipt_sha256", "outcome_receipt_sha256", "result_sha256", "error_code", "delivery_available", "updated_at")}
OPERATIONS = ("SELECT", "INSERT", "UPDATE", "REFERENCES")
SELECT, INSERT, UPDATE = (MappingProxyType(value) for value in (SELECT, INSERT, UPDATE))
MATRICES = MappingProxyType({"SELECT": SELECT, "INSERT": INSERT, "UPDATE": UPDATE, "REFERENCES": MappingProxyType({})})
_ATTRIBUTES = ("rolinherit", "rolsuper", "rolcreatedb", "rolcreaterole", "rolreplication", "rolbypassrls")
FUNCTIONS = ("app_tenant_id", "guard_owner_codex_append_only", "guard_owner_codex_task", "guard_owner_codex_event", "guard_owner_codex_event_presence")
_CATALOG_IDENTIFIERS = ("pg_roles", "pg_auth_members", "pg_shdepend", "pg_authid", "pg_class", "pg_namespace",
    "pg_attribute", "pg_proc", "pg_language", "pg_database", "current_database", "current_setting",
    "has_column_privilege", "has_table_privilege", "has_sequence_privilege", "has_schema_privilege",
    "has_function_privilege", "has_database_privilege", "pg_get_userbyid", "pg_get_function_identity_arguments",
    "aclexplode", "acldefault")


class PermissionDenied(ValueError):
    """Fixed failure codes; provider exceptions/credentials are not reflected."""
    def __init__(self, code):
        self.code = code if type(code) is str and re.fullmatch("[a-z][a-z0-9_]{0,79}", code) else "permission_contract_failed"
        super().__init__(self.code)


def _require(value, code):
    if not value:
        raise PermissionDenied(code)


@dataclass(frozen=True)
class PermissionTarget:
    database: str = DATABASE
    schema: str = SCHEMA
    schema_owner: str = SCHEMA_OWNER
    capability: str = CAPABILITY
    login: str = LOGIN

    def __post_init__(self):
        actual = (self.database, self.schema, self.schema_owner, self.capability, self.login)
        _require(type(self) is PermissionTarget and set(vars(self)) ==
                 {"database", "schema", "schema_owner", "capability", "login"}
                 and all(type(v) is str and re.fullmatch("[a-z][a-z0-9_]{0,62}", v) for v in actual)
                 and actual == (DATABASE, SCHEMA, SCHEMA_OWNER, CAPABILITY, LOGIN), "unregistered_permission_target")


def _target(target):
    _require(type(target) is PermissionTarget, "unregistered_permission_target")
    target.__post_init__()


def grant_statements(target):
    """Return an exact manifest; caller independently reviews/executes it.

    Trusted setup must first revoke PUBLIC/default grants on the blank test DB,
    schema and helper. No role, membership, CONNECT, password or revoke action
    is generated here. Trigger functions receive no explicit EXECUTE grant.
    """
    _target(target)
    result = [sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(sql.Identifier(target.schema), sql.Identifier(target.capability))]
    for operation, tables in (("SELECT", SELECT), ("INSERT", INSERT), ("UPDATE", UPDATE)):
        for table, columns in sorted(tables.items()):
            result.append(sql.SQL("GRANT {} ({}) ON TABLE {}.{} TO {}").format(
                sql.SQL(operation), sql.SQL(", ").join(map(sql.Identifier, columns)),
                sql.Identifier(target.schema), sql.Identifier(table), sql.Identifier(target.capability)))
    result.append(sql.SQL("GRANT EXECUTE ON FUNCTION {}.app_tenant_id() TO {}").format(
        sql.Identifier(target.schema), sql.Identifier(target.capability)))
    return tuple(result)


def _expected_columns():
    return {(operation, SCHEMA, table, column) for operation, tables in MATRICES.items()
            for table, columns in tables.items() for column in columns}


def _installer(identity):
    _require(type(identity) is str and re.fullmatch("[A-Za-z_][A-Za-z0-9_]{0,62}", identity)
        and identity == isolated.ADMIN, "unregistered_installer_identity")


def _row(value, keys):
    _require(type(value) is dict and set(value) == set(keys), "catalog_row_shape_mismatch")


def _rows(snapshot, key, keys, unique):
    rows = snapshot[key]
    _require(type(rows) is list, "catalog_rows_shape_mismatch")
    identities = []
    for row in rows:
        _row(row, keys)
        for field in unique:
            _require(type(row[field]) is str and (row[field] != "" or field == "arguments"), "catalog_identity_shape_mismatch")
        identities.append(tuple(row[k] for k in unique))
    _require(len(set(identities)) == len(identities), "duplicate_catalog_rows")
    return rows


def _paired(rows, dimensions):
    _require(all(r["role"] in (CAPABILITY, LOGIN) for r in rows), "catalog_effective_role_mismatch")
    groups = {}
    for row in rows:
        groups.setdefault(tuple(row[k] for k in dimensions), set()).add(row["role"])
    _require(all(pair == {CAPABILITY, LOGIN} for pair in groups.values()), "incomplete_effective_privilege_inventory")


def validate_catalog(snapshot, target, *, installer_identity, require_fresh_login=True, login_enabled=True):
    """Validate a complete read-only catalog snapshot, never owner/source rows."""
    _target(target)
    _installer(installer_identity)
    _require(type(require_fresh_login) is bool and type(login_enabled) is bool, "invalid_acceptance_mode")
    keys = {"identity", "installer", "bootstrap_role", "roles", "memberships", "owned_objects", "columns",
        "broad_relations", "sequences", "relation_acl", "column_acl", "schemas", "schema_acl", "functions",
        "function_acl", "databases", "database_acl", "task_relations"}
    _require(type(snapshot) is dict and set(snapshot) == keys, "catalog_snapshot_required")
    identity = snapshot["identity"]
    _row(identity, ("database", "host", "authenticated_user", "session_user", "current_user", "search_path"))
    _require(all(type(v) is str for v in identity.values()), "catalog_identity_shape_mismatch")
    _row(snapshot["installer"], ("role", "superuser"))
    _require(snapshot["installer"]["role"] == installer_identity and type(snapshot["installer"]["superuser"]) is bool,
        "installer_catalog_mismatch")
    bootstrap = snapshot["bootstrap_role"]
    _require(type(bootstrap) is str and re.fullmatch("[a-z][a-z0-9_]{0,62}", bootstrap), "bootstrap_role_mismatch")
    _require(identity["database"] == DATABASE and identity["host"] == HOST, "unregistered_database")
    if require_fresh_login:
        _require(login_enabled and identity["authenticated_user"] == LOGIN
            and identity["session_user"] == LOGIN and identity["current_user"] == CAPABILITY
            and identity["search_path"] == SCHEMA + ", pg_catalog", "fresh_login_identity_required")
    roles = _rows(snapshot, "roles", ("role", "rolcanlogin", "rolconnlimit", *_ATTRIBUTES), ("role",))
    _require(len(roles) == 3 and {r["role"] for r in roles} == {SCHEMA_OWNER, CAPABILITY, LOGIN}, "role_inventory_mismatch")
    for role in roles:
        _require(all(role[k] is False for k in _ATTRIBUTES)
            and role["rolcanlogin"] is (login_enabled if role["role"] == LOGIN else False)
            and type(role["rolconnlimit"]) is int and role["rolconnlimit"] == (LOGIN_CONNECTION_LIMIT if role["role"] == LOGIN else -1), "elevated_role_attributes")
    memberships = _rows(snapshot, "memberships", ("member", "role", "grantor", "admin_option", "inherit_option", "set_option"), ("member", "role", "grantor"))
    _require(all(type(r[k]) is bool for r in memberships for k in ("admin_option", "inherit_option", "set_option")), "membership_shape_mismatch")
    actual_memberships = {(r["member"], r["role"], r["grantor"], r["admin_option"], r["inherit_option"], r["set_option"]) for r in memberships}
    expected_memberships = {(LOGIN, CAPABILITY, installer_identity, False, False, True),
        (installer_identity, SCHEMA_OWNER, installer_identity, False, False, True)}
    if not snapshot["installer"]["superuser"]:
        expected_memberships.update((installer_identity, role, bootstrap, True, False, False) for role in (SCHEMA_OWNER, CAPABILITY, LOGIN))
    _require(actual_memberships == expected_memberships, "escalatable_role_membership")
    _require(snapshot["owned_objects"] == [], "runtime_role_owns_objects")
    effective = {CAPABILITY: set(), LOGIN: set()}
    columns = _rows(snapshot, "columns", ("role", "schema", "table", "column", "select", "insert", "update", "references"), ("role", "schema", "table", "column"))
    _paired(columns, ("schema", "table", "column"))
    for row in columns:
        _require(row["role"] in effective, "column_role_mismatch")
        for operation in OPERATIONS:
            value = row[operation.lower()]
            _require(type(value) is bool, "column_privilege_shape_mismatch")
            if value:
                effective[row["role"]].add((operation, row["schema"], row["table"], row["column"]))
    _require(effective[CAPABILITY] == _expected_columns() and effective[LOGIN] == set(), "effective_column_permissions_mismatch")
    _require(snapshot["broad_relations"] == [] and snapshot["sequences"] == [], "broad_or_sequence_privileges")
    relation_acl = _rows(snapshot, "relation_acl", ("table", "grantee", "grantor", "privilege", "grantable"), ("table", "grantee", "grantor", "privilege"))
    _require(all(r["table"] in SELECT and r["grantee"] == SCHEMA_OWNER and r["grantor"] == SCHEMA_OWNER
        and r["privilege"] in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")
        and r["grantable"] is False for r in relation_acl)
        and {(r["table"], r["privilege"]) for r in relation_acl} == {(t, p) for t in SELECT for p in
            ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER")}, "unregistered_relation_grants")
    column_acl = _rows(snapshot, "column_acl", ("table", "column", "grantee", "grantor", "privilege", "grantable"), ("table", "column", "grantee", "grantor", "privilege"))
    _require(all(r["grantee"] == CAPABILITY and r["grantor"] == SCHEMA_OWNER and r["grantable"] is False for r in column_acl)
        and {(r["privilege"], SCHEMA, r["table"], r["column"]) for r in column_acl} == _expected_columns(), "unregistered_column_grants")
    schemas = _rows(snapshot, "schemas", ("role", "schema", "usage", "create"), ("role", "schema"))
    _paired(schemas, ("schema",))
    _require(all(type(r["usage"]) is bool and r["create"] is False for r in schemas)
        and {(r["role"], r["schema"]) for r in schemas if r["usage"]} == {(CAPABILITY, SCHEMA)}, "schema_ddl_or_usage_privileges")
    schema_acl = _rows(snapshot, "schema_acl", ("grantee", "grantor", "privilege", "grantable"), ("grantee", "grantor", "privilege"))
    _require(all(r["grantor"] == SCHEMA_OWNER and r["grantable"] is False for r in schema_acl)
        and {(r["grantee"], r["privilege"]) for r in schema_acl} == {(SCHEMA_OWNER, "USAGE"), (SCHEMA_OWNER, "CREATE"), (CAPABILITY, "USAGE")}, "unregistered_schema_grants")
    functions = _rows(snapshot, "functions", ("role", "schema", "function", "arguments", "owner", "security_definer", "return_type", "language", "volatility", "execute"), ("role", "schema", "function", "arguments"))
    _paired(functions, ("schema", "function", "arguments"))
    _require(all(type(r["execute"]) is bool for r in functions)
        and {(r["role"], r["schema"], r["function"], r["arguments"]) for r in functions if r["execute"]}
            == {(CAPABILITY, SCHEMA, "app_tenant_id", "")}, "unintended_function_execution")
    helper = [r for r in functions if r["role"] == CAPABILITY and r["schema"] == SCHEMA and r["function"] == "app_tenant_id" and r["arguments"] == ""]
    _require(len(helper) == 1 and helper[0]["owner"] == SCHEMA_OWNER and helper[0]["security_definer"] is False
        and helper[0]["return_type"] == "uuid" and helper[0]["language"] == "sql" and helper[0]["volatility"] == "s", "tenant_helper_not_invoker")
    scoped_functions = [r for r in functions if r["schema"] == SCHEMA]
    _require({(r["role"], r["function"], r["arguments"]) for r in scoped_functions}
        == {(role, function, "") for role in (CAPABILITY, LOGIN) for function in FUNCTIONS}
        and all(r["owner"] == SCHEMA_OWNER and r["security_definer"] is False
            and r["return_type"] == ("uuid" if r["function"] == "app_tenant_id" else "trigger")
            and r["language"] == ("sql" if r["function"] == "app_tenant_id" else "plpgsql")
            and r["volatility"] == ("s" if r["function"] == "app_tenant_id" else "v") for r in scoped_functions), "metadata_function_inventory_mismatch")
    function_acl = _rows(snapshot, "function_acl", ("function", "arguments", "grantee", "grantor", "privilege", "grantable"), ("function", "arguments", "grantee", "grantor", "privilege"))
    _require(all(r["arguments"] == "" and r["privilege"] == "EXECUTE" and r["grantor"] == SCHEMA_OWNER and r["grantable"] is False for r in function_acl)
        and {(r["function"], r["grantee"]) for r in function_acl} == {(f, SCHEMA_OWNER) for f in FUNCTIONS} | {("app_tenant_id", CAPABILITY)}, "unregistered_function_grants")
    databases = _rows(snapshot, "databases", ("role", "database", "connect", "create", "temporary"), ("role", "database"))
    _paired(databases, ("database",))
    try:
        boundary.validate_database_privileges(databases)
    except boundary.BoundaryError:
        raise PermissionDenied("unintended_database_or_temp_privileges") from None
    database_acl = _rows(snapshot, "database_acl", ("grantee", "grantor", "privilege", "grantable"), ("grantee", "grantor", "privilege"))
    _require(all(r["grantor"] == SCHEMA_OWNER and r["grantable"] is False for r in database_acl)
        and {(r["grantee"], r["privilege"]) for r in database_acl} == {(SCHEMA_OWNER, "CONNECT"), (SCHEMA_OWNER, "CREATE"), (SCHEMA_OWNER, "TEMPORARY"), (LOGIN, "CONNECT"), (installer_identity, "CONNECT")}, "unregistered_database_grants")
    task_relations = _rows(snapshot, "task_relations", ("table", "owner", "rls", "force_rls"), ("table",))
    _require(len(task_relations) == 3 and {r["table"] for r in task_relations} == set(TASK_TABLES)
        and all(r["owner"] == SCHEMA_OWNER and r["rls"] is True and r["force_rls"] is True for r in task_relations), "metadata_rls_or_owner_mismatch")
    manifest = {"database": DATABASE, "schema": SCHEMA, "capability": CAPABILITY, "login": LOGIN,
        "column_privileges": sorted(_expected_columns()), "helper": "app_tenant_id()"}
    return {"status": "passed", "permission_manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "column_privileges_verified": len(_expected_columns()), "fresh_login_verified": require_fresh_login,
        "metadata_acl_verified": True, "end_user_authentication_verified": False, "protected_receipts_verified": False,
        "source_rows_read": 0, "source_rows_written": 0, "grants_executed": 0, "runtime_activated": False}


def verify_permissions(connection, target, *, installer_identity, require_fresh_login=True, login_enabled=True):
    """Read only catalogs using named effective-role privilege checks.

    Source-only setup may select a pre-login catalog audit. Default acceptance
    requires a genuine fresh restricted session, never administrator SET ROLE.
    Returns fixed metadata only; caller must sanitize unexpected DB errors.
    """
    _target(target)
    _installer(installer_identity)
    _require(type(require_fresh_login) is bool and type(login_enabled) is bool, "invalid_acceptance_mode")
    user_ns = "n.nspname !~ '^pg_' AND n.nspname<>'information_schema'"
    pair = [CAPABILITY, LOGIN]
    def execute(query, args=()):
        # Fixed compiler-owned catalog names only: never resolve them through
        # the disposable schema or an existing temporary relation/function.
        qualified = re.sub(r"(?<![\w.])(" + "|".join(_CATALOG_IDENTIFIERS) + r")\b",
            lambda match: "pg_catalog." + match.group(0), query)
        return connection.execute(qualified, args)
    def all_rows(query, args=()):
        rows = execute(query, args).fetchall()
        _require(type(rows) is list and all(type(r) is dict for r in rows), "catalog_rows_shape_mismatch")
        return rows
    snapshot = {}
    identity = execute("SELECT current_database() AS database,session_user::text AS session_user,current_user::text AS current_user,current_setting('search_path') AS search_path").fetchone()
    _row(identity, ("database", "session_user", "current_user", "search_path"))
    identity = dict(identity)
    identity["host"] = connection.info.host
    identity["authenticated_user"] = connection.info.user
    snapshot["identity"] = identity
    installer = execute("SELECT rolname AS role,rolsuper AS superuser FROM pg_roles WHERE rolname=%s", (installer_identity,)).fetchone()
    _row(installer, ("role", "superuser"))
    snapshot["installer"] = dict(installer)
    bootstrap = execute("SELECT rolname AS role FROM pg_roles WHERE oid=10").fetchone()
    _row(bootstrap, ("role",))
    snapshot["bootstrap_role"] = bootstrap["role"]
    snapshot["roles"] = all_rows("SELECT rolname AS role,rolcanlogin,rolinherit,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolconnlimit FROM pg_roles WHERE rolname=ANY(%s) ORDER BY rolname", ([SCHEMA_OWNER, CAPABILITY, LOGIN],))
    snapshot["memberships"] = all_rows("""SELECT member.rolname AS member,parent.rolname AS role,grantor.rolname AS grantor,m.admin_option,m.inherit_option,m.set_option
        FROM pg_auth_members m JOIN pg_roles member ON member.oid=m.member JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles grantor ON grantor.oid=m.grantor
        WHERE member.rolname=ANY(%s) OR parent.rolname=ANY(%s) ORDER BY member.rolname,parent.rolname,m.grantor""", ([SCHEMA_OWNER, CAPABILITY, LOGIN], [SCHEMA_OWNER, CAPABILITY, LOGIN]))
    snapshot["owned_objects"] = all_rows("SELECT d.classid::regclass::text AS catalog,d.objid FROM pg_shdepend d WHERE d.refclassid='pg_authid'::regclass AND d.refobjid IN (SELECT oid FROM pg_roles WHERE rolname=ANY(%s)) AND d.deptype='o'", (pair,))
    snapshot["columns"] = all_rows(f"""SELECT r.rolname AS role,n.nspname AS schema,t.relname AS "table",a.attname AS "column",
        has_column_privilege(r.oid,t.oid,a.attnum,'SELECT') AS "select",has_column_privilege(r.oid,t.oid,a.attnum,'INSERT') AS "insert",
        has_column_privilege(r.oid,t.oid,a.attnum,'UPDATE') AS "update",has_column_privilege(r.oid,t.oid,a.attnum,'REFERENCES') AS "references"
        FROM pg_roles r CROSS JOIN pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        JOIN pg_attribute a ON a.attrelid=t.oid AND a.attnum>0 AND NOT a.attisdropped
        WHERE r.rolname=ANY(%s) AND t.relkind IN ('r','p','v','m','f') AND {user_ns}""", (pair,))
    snapshot["broad_relations"] = all_rows(f"""SELECT r.rolname,t.oid FROM pg_roles r CROSS JOIN pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE r.rolname=ANY(%s) AND t.relkind IN ('r','p','v','m','f') AND {user_ns}
        AND has_table_privilege(r.oid,t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER')""", (pair,))
    snapshot["sequences"] = all_rows(f"""SELECT r.rolname,t.oid FROM pg_roles r CROSS JOIN pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace
        WHERE r.rolname=ANY(%s) AND t.relkind='S' AND {user_ns} AND has_sequence_privilege(r.oid,t.oid,'USAGE,SELECT,UPDATE')""", (pair,))
    snapshot["relation_acl"] = all_rows(f"""SELECT t.relname AS "table",pg_get_userbyid(a.grantee) AS grantee,pg_get_userbyid(a.grantor) AS grantor,a.privilege_type AS privilege,a.is_grantable AS grantable FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace,
        LATERAL aclexplode(coalesce(t.relacl,acldefault('r',t.relowner))) a
        WHERE n.nspname=%s AND t.relname=ANY(%s)""", (SCHEMA, list(SELECT)))
    snapshot["column_acl"] = all_rows("""SELECT t.relname AS "table",c.attname AS "column",pg_get_userbyid(a.grantee) AS grantee,pg_get_userbyid(a.grantor) AS grantor,a.privilege_type AS privilege,a.is_grantable AS grantable
        FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace JOIN pg_attribute c ON c.attrelid=t.oid,
        LATERAL aclexplode(c.attacl) a WHERE n.nspname=%s AND t.relname=ANY(%s)""", (SCHEMA, list(SELECT)))
    snapshot["schemas"] = all_rows(f"""SELECT r.rolname AS role,n.nspname AS schema,has_schema_privilege(r.oid,n.oid,'USAGE') AS usage,
        has_schema_privilege(r.oid,n.oid,'CREATE') AS "create" FROM pg_roles r CROSS JOIN pg_namespace n WHERE r.rolname=ANY(%s) AND {user_ns}""", (pair,))
    snapshot["schema_acl"] = all_rows("""SELECT pg_get_userbyid(a.grantee) AS grantee,pg_get_userbyid(a.grantor) AS grantor,a.privilege_type AS privilege,a.is_grantable AS grantable FROM pg_namespace n,
        LATERAL aclexplode(coalesce(n.nspacl,acldefault('n',n.nspowner))) a WHERE n.nspname=%s""", (SCHEMA,))
    snapshot["functions"] = all_rows(f"""SELECT r.rolname AS role,n.nspname AS schema,p.proname AS "function",pg_get_function_identity_arguments(p.oid) AS arguments,
        pg_get_userbyid(p.proowner) AS owner,p.prosecdef AS security_definer,p.prorettype::regtype::text AS return_type,l.lanname AS language,
        p.provolatile::text AS volatility,has_function_privilege(r.oid,p.oid,'EXECUTE') AS execute
        FROM pg_roles r CROSS JOIN pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_language l ON l.oid=p.prolang
        WHERE r.rolname=ANY(%s) AND {user_ns}""", (pair,))
    snapshot["function_acl"] = all_rows("""SELECT p.proname AS "function",pg_get_function_identity_arguments(p.oid) AS arguments,pg_get_userbyid(a.grantee) AS grantee,pg_get_userbyid(a.grantor) AS grantor,a.privilege_type AS privilege,a.is_grantable AS grantable FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace,
        LATERAL aclexplode(coalesce(p.proacl,acldefault('f',p.proowner))) a WHERE n.nspname=%s""", (SCHEMA,))
    snapshot["databases"] = all_rows("""SELECT r.rolname AS role,d.datname AS database,has_database_privilege(r.oid,d.oid,'CONNECT') AS connect,
        has_database_privilege(r.oid,d.oid,'CREATE') AS "create",has_database_privilege(r.oid,d.oid,'TEMPORARY') AS temporary
        FROM pg_roles r CROSS JOIN pg_database d WHERE r.rolname=ANY(%s) AND d.datallowconn""", (pair,))
    snapshot["database_acl"] = all_rows("""SELECT pg_get_userbyid(a.grantee) AS grantee,pg_get_userbyid(a.grantor) AS grantor,a.privilege_type AS privilege,a.is_grantable AS grantable FROM pg_database d,
        LATERAL aclexplode(coalesce(d.datacl,acldefault('d',d.datdba))) a WHERE d.datname=%s""", (DATABASE,))
    snapshot["task_relations"] = all_rows("""SELECT t.relname AS "table",pg_get_userbyid(t.relowner) AS owner,t.relrowsecurity AS rls,t.relforcerowsecurity AS force_rls
        FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname=%s AND t.relname=ANY(%s)""", (SCHEMA, list(TASK_TABLES)))
    return validate_catalog(snapshot, target, installer_identity=installer_identity, require_fresh_login=require_fresh_login, login_enabled=login_enabled)
