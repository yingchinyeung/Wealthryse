"""INERT, complete bounded catalog observation for ONE new blank host.

The trusted wrapper supplies a fresh administrator connection and enforces
verify-full transport. This module opens nothing, reads no environment and
executes only qualified catalog SELECTs. TLS here means libpq reports encryption;
it is not independent certificate, owner authentication or process-isolation proof.
No table rows, passwords, function bodies or definitions are returned. Function
source/definition/configuration fingerprints are observations, NOT semantic
approval. All routines are included, not only a guessed provider-name allowlist.
The record is private untrusted metadata. Unknown database names need review;
even familiar provider names do NOT permit a PUBLIC privilege exception.
Multiple autocommit reads are not an atomic cluster-wide snapshot. Every query
has MAX+1 overflow detection, and complete canonical output shares the codec's
4 MiB cap. Individual protected archive receipts remain separately bounded;
transport framing does not establish actual Azure delivery or retention.
No truncation, sampling, protected writer, setup, grant, cleanup, model or CLI.
"""
import hashlib
import json
import re

from ibkr_etl import owner_codex_isolated_target as registry
from ibkr_etl.owner_codex_catalog_transport import MAX_RECORD_BYTES


PREFIX = "OWNER_TASK_ISOLATED_PROVIDER "
DEFAULT_DATABASE_NAMES = frozenset({"postgres", "template0", "template1", "azure_maintenance", "azure_sys"})
_SHA = re.compile(r"[0-9a-f]{64}")
_PRIVILEGE = re.compile(r"[A-Z][A-Z ]{0,31}")


class ProviderObservationError(ValueError):
    def __init__(self, code):
        allowed = {"provider_connection_mismatch", "provider_connection_not_idle", "provider_catalog_path_unsafe",
            "provider_identity_mismatch", "provider_catalog_shape_invalid", "provider_catalog_overflow",
            "provider_catalog_reference_invalid", "provider_record_mismatch", "provider_record_oversized",
            "provider_observation_failed"}
        self.code = code if type(code) is str and code in allowed else "provider_observation_failed"
        super().__init__(self.code)


def _require(condition, code):
    if not condition:
        raise ProviderObservationError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
                      allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def _hash_sql(expression):
    # Expressions are fixed source strings, never caller input or SQL identifiers.
    return "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(" + expression + ", 'UTF8')), 'hex')"


PATH_SQL = "SELECT pg_catalog.current_setting('search_path') AS search_path,pg_catalog.pg_my_temp_schema() AS temporary_schema"
IDENTITY_SQL = ("SELECT pg_catalog.current_database() AS database,current_user AS role,session_user AS login,"
    "pg_catalog.current_setting('server_version_num') AS version,"
    "pg_catalog.current_setting('transaction_read_only') AS readonly,pg_catalog.pg_backend_pid() AS backend_pid")
_USER_NS = ("n.nspname NOT IN ('pg_catalog','information_schema','pg_toast') "
            "AND n.nspname !~ '^pg_(toast_)?temp_[0-9]+$'")


# Each specification is: row cap, ordering/uniqueness key, exact field validators,
# and one fixed SELECT. LIMIT cap+1 distinguishes complete data from truncation.
SPECS = {
    "databases": (64, ("oid",), {
        "oid": "oid", "name": "name", "owner_oid": "oid", "allow_connections": "bool",
        "is_template": "bool", "connection_limit": "limit", "marker_sha256": "nullable_sha"},
        "SELECT d.oid::pg_catalog.int8 AS oid,d.datname AS name,d.datdba::pg_catalog.int8 AS owner_oid,"
        "d.datallowconn AS allow_connections,d.datistemplate AS is_template,d.datconnlimit AS connection_limit,"
        + _hash_sql("pg_catalog.shobj_description(d.oid,'pg_database')") + " AS marker_sha256 "
        "FROM pg_catalog.pg_database d ORDER BY d.oid LIMIT 65"),
    "database_acls": (1024, ("object_oid", "grantee_oid", "grantor_oid", "privilege"), {
        "object_oid": "oid", "grantee_oid": "grantee", "grantor_oid": "oid", "privilege": "privilege",
        "grantable": "bool"},
        "SELECT d.oid::pg_catalog.int8 AS object_oid,a.grantee::pg_catalog.int8 AS grantee_oid,"
        "a.grantor::pg_catalog.int8 AS grantor_oid,a.privilege_type AS privilege,a.is_grantable AS grantable "
        "FROM pg_catalog.pg_database d CROSS JOIN LATERAL pg_catalog.aclexplode("
        "COALESCE(d.datacl,pg_catalog.acldefault('d',d.datdba))) a "
        "ORDER BY d.oid,a.grantee,a.grantor,a.privilege_type LIMIT 1025"),
    "roles": (256, ("oid",), {
        "oid": "oid", "name": "name", "can_login": "bool", "inherit": "bool", "superuser": "bool",
        "create_database": "bool", "create_role": "bool", "replication": "bool", "bypass_rls": "bool",
        "connection_limit": "limit", "valid_until": "nullable_text", "configuration_sha256": "nullable_sha",
        "marker_sha256": "nullable_sha"},
        "SELECT r.oid::pg_catalog.int8 AS oid,r.rolname AS name,r.rolcanlogin AS can_login,"
        "r.rolinherit AS inherit,r.rolsuper AS superuser,r.rolcreatedb AS create_database,"
        "r.rolcreaterole AS create_role,r.rolreplication AS replication,r.rolbypassrls AS bypass_rls,"
        "r.rolconnlimit AS connection_limit,r.rolvaliduntil::pg_catalog.text AS valid_until,"
        + _hash_sql("r.rolconfig::pg_catalog.text") + " AS configuration_sha256,"
        + _hash_sql("pg_catalog.shobj_description(r.oid,'pg_authid')") + " AS marker_sha256 "
        "FROM pg_catalog.pg_roles r ORDER BY r.oid LIMIT 257"),
    "memberships": (1024, ("member_oid", "role_oid", "grantor_oid"), {
        "member_oid": "oid", "role_oid": "oid", "grantor_oid": "oid", "admin_option": "bool",
        "inherit_option": "bool", "set_option": "bool"},
        "SELECT m.member::pg_catalog.int8 AS member_oid,m.roleid::pg_catalog.int8 AS role_oid,"
        "m.grantor::pg_catalog.int8 AS grantor_oid,m.admin_option,m.inherit_option,m.set_option "
        "FROM pg_catalog.pg_auth_members m ORDER BY m.member,m.roleid,m.grantor LIMIT 1025"),
    "schemas": (128, ("oid",), {"oid": "oid", "name": "name", "owner_oid": "oid"},
        "SELECT n.oid::pg_catalog.int8 AS oid,n.nspname AS name,n.nspowner::pg_catalog.int8 AS owner_oid "
        "FROM pg_catalog.pg_namespace n ORDER BY n.oid LIMIT 129"),
    "schema_acls": (2048, ("object_oid", "grantee_oid", "grantor_oid", "privilege"), {
        "object_oid": "oid", "grantee_oid": "grantee", "grantor_oid": "oid", "privilege": "privilege",
        "grantable": "bool"},
        "SELECT n.oid::pg_catalog.int8 AS object_oid,a.grantee::pg_catalog.int8 AS grantee_oid,"
        "a.grantor::pg_catalog.int8 AS grantor_oid,a.privilege_type AS privilege,a.is_grantable AS grantable "
        "FROM pg_catalog.pg_namespace n CROSS JOIN LATERAL pg_catalog.aclexplode("
        "COALESCE(n.nspacl,pg_catalog.acldefault('n',n.nspowner))) a "
        "ORDER BY n.oid,a.grantee,a.grantor,a.privilege_type LIMIT 2049"),
    "default_acls": (1024, ("oid",), {
        "oid": "oid", "owner_oid": "oid", "schema_oid": "grantee", "object_type": "char",
        "acl_sha256": "sha"},
        "SELECT d.oid::pg_catalog.int8 AS oid,d.defaclrole::pg_catalog.int8 AS owner_oid,"
        "d.defaclnamespace::pg_catalog.int8 AS schema_oid,d.defaclobjtype AS object_type,"
        + _hash_sql("d.defaclacl::pg_catalog.text") + " AS acl_sha256 "
        "FROM pg_catalog.pg_default_acl d ORDER BY d.oid LIMIT 1025"),
    "default_acl_grants": (4096, ("object_oid", "grantee_oid", "grantor_oid", "privilege"), {
        "object_oid": "oid", "grantee_oid": "grantee", "grantor_oid": "oid", "privilege": "privilege",
        "grantable": "bool"},
        "SELECT d.oid::pg_catalog.int8 AS object_oid,a.grantee::pg_catalog.int8 AS grantee_oid,"
        "a.grantor::pg_catalog.int8 AS grantor_oid,a.privilege_type AS privilege,a.is_grantable AS grantable "
        "FROM pg_catalog.pg_default_acl d CROSS JOIN LATERAL pg_catalog.aclexplode(d.defaclacl) a "
        "ORDER BY d.oid,a.grantee,a.grantor,a.privilege_type LIMIT 4097"),
    "extensions": (128, ("oid",), {
        "oid": "oid", "name": "name", "owner_oid": "oid", "schema_oid": "oid", "version": "text",
        "relocatable": "bool"},
        "SELECT e.oid::pg_catalog.int8 AS oid,e.extname AS name,e.extowner::pg_catalog.int8 AS owner_oid,"
        "e.extnamespace::pg_catalog.int8 AS schema_oid,e.extversion AS version,e.extrelocatable AS relocatable "
        "FROM pg_catalog.pg_extension e ORDER BY e.oid LIMIT 129"),
    "relations": (4096, ("oid",), {
        "oid": "oid", "name": "name", "owner_oid": "oid", "schema_oid": "oid", "kind": "char",
        "persistence": "char", "row_security": "bool", "force_row_security": "bool", "acl_sha256": "nullable_sha"},
        "SELECT c.oid::pg_catalog.int8 AS oid,c.relname AS name,c.relowner::pg_catalog.int8 AS owner_oid,"
        "c.relnamespace::pg_catalog.int8 AS schema_oid,c.relkind AS kind,c.relpersistence AS persistence,"
        "c.relrowsecurity AS row_security,c.relforcerowsecurity AS force_row_security,"
        + _hash_sql("c.relacl::pg_catalog.text") + " AS acl_sha256 "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace WHERE "
        + _USER_NS + " ORDER BY c.oid LIMIT 4097"),
    "relation_acls": (8192, ("object_oid", "grantee_oid", "grantor_oid", "privilege"), {
        "object_oid": "oid", "grantee_oid": "grantee", "grantor_oid": "oid", "privilege": "privilege",
        "grantable": "bool"},
        "SELECT c.oid::pg_catalog.int8 AS object_oid,a.grantee::pg_catalog.int8 AS grantee_oid,"
        "a.grantor::pg_catalog.int8 AS grantor_oid,a.privilege_type AS privilege,a.is_grantable AS grantable "
        "FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN LATERAL pg_catalog.aclexplode(COALESCE(c.relacl,pg_catalog.acldefault("
        "CASE WHEN c.relkind='S' THEN 's'::pg_catalog.\"char\" ELSE 'r'::pg_catalog.\"char\" END,c.relowner))) a "
        "WHERE " + _USER_NS + " ORDER BY c.oid,a.grantee,a.grantor,a.privilege_type LIMIT 8193"),
    "routines": (4096, ("oid",), {
        "oid": "oid", "name": "name", "owner_oid": "oid", "schema_oid": "oid", "kind": "char",
        "identity_arguments": "arguments", "language_oid": "oid", "language": "name",
        "language_trusted": "bool", "security_definer": "bool", "leakproof": "bool",
        "volatility": "char", "parallel": "char", "configuration_present": "bool",
        "configuration_count": "count", "configuration_sha256": "nullable_sha",
        "body_sha256": "sha", "binary_sha256": "nullable_sha", "definition_sha256": "nullable_sha",
        "acl_sha256": "nullable_sha"},
        "SELECT p.oid::pg_catalog.int8 AS oid,p.proname AS name,p.proowner::pg_catalog.int8 AS owner_oid,"
        "p.pronamespace::pg_catalog.int8 AS schema_oid,p.prokind AS kind,"
        "pg_catalog.pg_get_function_identity_arguments(p.oid) AS identity_arguments,"
        "p.prolang::pg_catalog.int8 AS language_oid,l.lanname AS language,l.lanpltrusted AS language_trusted,"
        "p.prosecdef AS security_definer,p.proleakproof AS leakproof,p.provolatile AS volatility,"
        "p.proparallel AS parallel,p.proconfig IS NOT NULL AS configuration_present,"
        "COALESCE(pg_catalog.array_length(p.proconfig,1),0) AS configuration_count,"
        + _hash_sql("p.proconfig::pg_catalog.text") + " AS configuration_sha256,"
        + _hash_sql("p.prosrc") + " AS body_sha256,"
        + _hash_sql("p.probin") + " AS binary_sha256,CASE WHEN p.prokind='a' THEN NULL ELSE "
        + _hash_sql("pg_catalog.pg_get_functiondef(p.oid)") + " END AS definition_sha256,"
        + _hash_sql("p.proacl::pg_catalog.text") + " AS acl_sha256 "
        "FROM pg_catalog.pg_proc p JOIN pg_catalog.pg_language l ON l.oid=p.prolang ORDER BY p.oid LIMIT 4097"),
    "routine_acls": (16384, ("object_oid", "grantee_oid", "grantor_oid", "privilege"), {
        "object_oid": "oid", "grantee_oid": "grantee", "grantor_oid": "oid", "privilege": "privilege",
        "grantable": "bool"},
        "SELECT p.oid::pg_catalog.int8 AS object_oid,a.grantee::pg_catalog.int8 AS grantee_oid,"
        "a.grantor::pg_catalog.int8 AS grantor_oid,a.privilege_type AS privilege,a.is_grantable AS grantable "
        "FROM pg_catalog.pg_proc p CROSS JOIN LATERAL pg_catalog.aclexplode("
        "COALESCE(p.proacl,pg_catalog.acldefault('f',p.proowner))) a "
        "ORDER BY p.oid,a.grantee,a.grantor,a.privilege_type LIMIT 16385"),
}


def _text(value, limit):
    return type(value) is str and len(value.encode("utf-8")) <= limit and all(c.isprintable() for c in value)


def _valid(value, kind):
    if kind == "bool": return type(value) is bool
    if kind in {"oid", "grantee"}:
        return type(value) is int and (1 if kind == "oid" else 0) <= value <= 4294967295
    if kind == "limit": return type(value) is int and -1 <= value <= 2147483647
    if kind == "count": return type(value) is int and 0 <= value <= 1024
    if kind == "name": return _text(value, 63) and bool(value)
    if kind == "text": return _text(value, 256) and bool(value)
    if kind == "nullable_text": return value is None or _text(value, 80)
    if kind == "char": return type(value) is str and len(value) == 1 and value.isascii() and value.isprintable()
    if kind == "privilege": return type(value) is str and _PRIVILEGE.fullmatch(value) is not None
    if kind == "sha": return type(value) is str and _SHA.fullmatch(value) is not None
    if kind == "nullable_sha": return value is None or _valid(value, "sha")
    if kind == "arguments": return _text(value, 4096)
    return False


def _validate_catalog(catalog):
    _require(type(catalog) is dict and set(catalog) == set(SPECS), "provider_catalog_shape_invalid")
    for name, (limit, ordering, fields, _) in SPECS.items():
        rows = catalog[name]
        _require(type(rows) is list and len(rows) <= limit, "provider_catalog_overflow")
        _require(all(type(row) is dict and set(row) == set(fields)
            and all(_valid(row[k], v) for k, v in fields.items()) for row in rows), "provider_catalog_shape_invalid")
        keys = [tuple(row[k] for k in ordering) for row in rows]
        _require(keys == sorted(set(keys)), "provider_catalog_shape_invalid")
    roles = {r["oid"] for r in catalog["roles"]}
    schemas = {r["oid"] for r in catalog["schemas"]}
    _require(roles and schemas and catalog["databases"], "provider_catalog_shape_invalid")
    _require(any(r["name"] == registry.ADMIN for r in catalog["roles"])
        and any(r["name"] == registry.MANAGEMENT_DATABASE for r in catalog["databases"])
        and any(r["name"] == "pg_catalog" for r in catalog["schemas"]), "provider_catalog_reference_invalid")
    for name in ("databases", "schemas", "default_acls", "extensions", "relations", "routines"):
        for row in catalog[name]:
            _require(row["owner_oid"] in roles, "provider_catalog_reference_invalid")
            if "schema_oid" in row:
                _require(row["schema_oid"] in schemas or name == "default_acls" and row["schema_oid"] == 0,
                         "provider_catalog_reference_invalid")
    for row in catalog["memberships"]:
        _require(all(row[k] in roles for k in ("member_oid", "role_oid", "grantor_oid")), "provider_catalog_reference_invalid")
    for row in catalog["routines"]:
        _require((row["configuration_present"] and row["configuration_sha256"] is not None)
            or (not row["configuration_present"] and row["configuration_sha256"] is None
                and row["configuration_count"] == 0), "provider_catalog_shape_invalid")
    for kind, objects in (("database_acls", "databases"), ("schema_acls", "schemas"),
                         ("default_acl_grants", "default_acls"), ("relation_acls", "relations"), ("routine_acls", "routines")):
        oids = {r["oid"] for r in catalog[objects]}
        for row in catalog[kind]:
            _require(row["object_oid"] in oids and row["grantor_oid"] in roles
                and (row["grantee_oid"] == 0 or row["grantee_oid"] in roles), "provider_catalog_reference_invalid")
    for name in ("roles", "schemas", "databases", "extensions"):
        _require(len({r["name"] for r in catalog[name]}) == len(catalog[name]), "provider_catalog_shape_invalid")
    return catalog


def _path(raw):
    row = raw.execute(PATH_SQL).fetchone()
    _require(type(row) is dict and set(row) == {"search_path", "temporary_schema"}
        and type(row["search_path"]) is str and row["search_path"] == "pg_catalog" and type(row["temporary_schema"]) is int
        and row["temporary_schema"] == 0, "provider_catalog_path_unsafe")


def _identity(raw):
    row = raw.execute(IDENTITY_SQL).fetchone()
    _require(type(row) is dict and set(row) == {"database", "role", "login", "version", "readonly", "backend_pid"}
        and all(type(row[k]) is str for k in ("database", "role", "login", "version", "readonly"))
        and row["database"] == registry.MANAGEMENT_DATABASE
        and row["role"] == row["login"] == registry.ADMIN
        and type(row["version"]) is str and re.fullmatch(r"16[0-9]{4}", row["version"]) is not None
        and row["readonly"] == "on" and type(row["backend_pid"]) is int
        and 0 < row["backend_pid"] <= 2147483647 and type(raw.info.backend_pid) is int
        and row["backend_pid"] == raw.info.backend_pid,
        "provider_identity_mismatch")
    return row


def validate_record(record):
    target = registry.registered_target()
    fixed = dict(status="observed", host=target.host, administrator_login=target.administrator_login,
        management_database=target.management_database, registration_sha256=target.registration_sha256,
        physical_scope_sha256=target.physical_scope_sha256, postgres_major=16, readonly_verified=True,
        tls_verified=True, safe_catalog_path_verified=True, public_acl_grantee_oid=0, review_required=True,
        setup_eligible=False, restricted_setup_authorized=False, provider_semantics_verified=False,
        system_database_privilege_exception_allowed=False, catalog_consistent_snapshot_verified=False,
        fresh_restricted_login_verified=False, model_credential_isolation_verified=False,
        authenticated_owner_verified=False, protected_retention_verified=False, production_database_touched=False,
        source_rows_read=0, source_rows_written=0, ddl_executed=0, grants_changed=0, model_calls=0, agents_activated=0)
    dynamic = {"catalog", "catalog_sha256", "catalog_counts", "unrecognized_database_names", "future_trial_database_present",
               "future_trial_roles_present", "server_version_num", "definition_unavailable_routine_oids"}
    _require(type(record) is dict and set(record) == set(fixed) | dynamic, "provider_record_mismatch")
    _require(all(type(record[k]) is type(v) and record[k] == v for k, v in fixed.items()), "provider_record_mismatch")
    catalog = _validate_catalog(record["catalog"])
    expected = dict(catalog_sha256=_digest(catalog), catalog_counts={k: len(v) for k, v in catalog.items()},
        unrecognized_database_names=sorted(r["name"] for r in catalog["databases"] if r["name"] not in DEFAULT_DATABASE_NAMES),
        future_trial_database_present=any(r["name"] == target.future_database for r in catalog["databases"]),
        future_trial_roles_present=sorted(r["name"] for r in catalog["roles"]
            if r["name"] in (target.schema_owner, target.capability, target.login)),
        definition_unavailable_routine_oids=[r["oid"] for r in catalog["routines"] if r["definition_sha256"] is None])
    _require(all(type(record[k]) is type(v) and canonical(record[k]) == canonical(v) for k, v in expected.items()),
             "provider_record_mismatch")
    _require(type(record["server_version_num"]) is str and re.fullmatch(r"16[0-9]{4}", record["server_version_num"]) is not None,
             "provider_record_mismatch")
    _require(len(canonical(record)) <= MAX_RECORD_BYTES, "provider_record_oversized")
    return record


def inspect_provider(raw):
    """Observe only; exceptions expose fixed codes, never database error text."""
    try:
        target = registry.registered_target()
        _require(raw.autocommit is True and isinstance(raw.info.transaction_status, int)
            and type(raw.info.transaction_status) is not bool
            and raw.info.transaction_status == 0, "provider_connection_not_idle")
        _require(all(type(getattr(raw.info, k)) is str for k in ("host", "dbname", "user"))
            and raw.info.host == target.host and raw.info.dbname == target.management_database
            and raw.info.user == target.administrator_login and raw.pgconn.ssl_in_use is True,
            "provider_connection_mismatch")
        _path(raw)  # Qualified safe path/temp query BEFORE any catalog casts.
        identity = _identity(raw)
        catalog = {}
        for name, (limit, _, _, query) in SPECS.items():
            rows = raw.execute(query).fetchall()
            _require(type(rows) is list and len(rows) <= limit, "provider_catalog_overflow")
            catalog[name] = rows
        _validate_catalog(catalog)
        _path(raw)
        _require(_identity(raw) == identity and raw.autocommit is True
            and isinstance(raw.info.transaction_status, int)
            and type(raw.info.transaction_status) is not bool and raw.info.transaction_status == 0,
            "provider_identity_mismatch")
        record = dict(status="observed", host=target.host, administrator_login=target.administrator_login,
            management_database=target.management_database, registration_sha256=target.registration_sha256,
            physical_scope_sha256=target.physical_scope_sha256, postgres_major=16, readonly_verified=True,
            tls_verified=True, safe_catalog_path_verified=True, public_acl_grantee_oid=0, review_required=True,
            setup_eligible=False, restricted_setup_authorized=False, provider_semantics_verified=False,
            system_database_privilege_exception_allowed=False, catalog_consistent_snapshot_verified=False,
            fresh_restricted_login_verified=False, model_credential_isolation_verified=False,
            authenticated_owner_verified=False, protected_retention_verified=False, production_database_touched=False,
            source_rows_read=0, source_rows_written=0, ddl_executed=0, grants_changed=0, model_calls=0, agents_activated=0,
            catalog=catalog, catalog_sha256=_digest(catalog), catalog_counts={k: len(v) for k, v in catalog.items()},
            unrecognized_database_names=sorted(r["name"] for r in catalog["databases"] if r["name"] not in DEFAULT_DATABASE_NAMES),
            future_trial_database_present=any(r["name"] == target.future_database for r in catalog["databases"]),
            future_trial_roles_present=sorted(r["name"] for r in catalog["roles"]
                if r["name"] in (target.schema_owner, target.capability, target.login)),
            server_version_num=identity["version"],
            definition_unavailable_routine_oids=[r["oid"] for r in catalog["routines"] if r["definition_sha256"] is None])
        return validate_record(record)
    except ProviderObservationError:
        raise
    except Exception:
        raise ProviderObservationError("provider_observation_failed") from None
