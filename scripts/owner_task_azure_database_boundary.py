"""Inert Azure-specific step-2 permission policy approved by the human owner.

Only the registered private host's two Microsoft-owned databases are exceptions
to CONNECT/TEMPORARY metadata checks. No SQL, credential read, model call or
installation occurs here. Input observations are untrusted until a trusted
controller verifies their protected receipts and fresh native provenance.
The original collector/old shared-host policy remain unchanged. This review
never turns administrator TLS or catalog metadata into fresh-login acceptance.
"""
from dataclasses import dataclass
import hashlib
import json

from psycopg import sql

import inspect_owner_task_isolated_provider as collector
from ibkr_etl import owner_codex_isolated_target as target


POLICY = "owner-task-azure-private-boundary-20261005-v1"
APPROVED_CATALOG_SHA256 = "873b73ba8c3eb686850aa82d1081c14dcd0a69a0b220122041b32d90aa6eeea6"
PROVIDER_DATABASES = frozenset(("azure_maintenance", "azure_sys"))
CUSTOMER_DATABASES = frozenset(("postgres", "template1"))
ROLES = frozenset((target.LOGIN, target.CAPABILITY))


class BoundaryError(ValueError):
    """Fixed codes only; never reflect SQL, connection or sensitive payloads."""
    def __init__(self, code):
        self.code = code if type(code) is str and code in {
            "boundary_target_mismatch", "boundary_catalog_unreviewed",
            "boundary_privilege_inventory_invalid", "boundary_privilege_exceeded",
            "boundary_behavior_unaccepted", "boundary_installer_mismatch",
        } else "boundary_behavior_unaccepted"
        super().__init__(self.code)


def require(value, code):
    if not value:
        raise BoundaryError(code)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False).encode()).hexdigest()


@dataclass(frozen=True)
class Scope:
    host: str = target.HOST
    database: str = target.DATABASE
    login: str = target.LOGIN
    capability: str = target.CAPABILITY
    schema_owner: str = target.SCHEMA_OWNER
    schema: str = target.SCHEMA

    def __post_init__(self):
        expected = dict(host=target.HOST, database=target.DATABASE, login=target.LOGIN,
            capability=target.CAPABILITY, schema_owner=target.SCHEMA_OWNER, schema=target.SCHEMA)
        require(type(self) is Scope and vars(self) == expected
            and all(type(v) is str for v in vars(self).values()), "boundary_target_mismatch")


def checked_scope(scope):
    require(type(scope) is Scope, "boundary_target_mismatch")
    scope.__post_init__()
    return Scope()


def review_catalog(record, scope=Scope()):
    """Review ONE accepted observation; no name-based exception for future drift."""
    checked_scope(scope)
    try:
        collector.validate_record(record)
    except Exception:
        raise BoundaryError("boundary_catalog_unreviewed") from None
    require(record["catalog_sha256"] == APPROVED_CATALOG_SHA256,
        "boundary_catalog_unreviewed")
    catalog = record["catalog"]
    roles = {r["name"]: r for r in catalog["roles"]}
    databases = {d["name"]: d for d in catalog["databases"]}
    require(set(databases) == CUSTOMER_DATABASES | PROVIDER_DATABASES | {"template0"}
        and roles["azuresu"]["superuser"] is True
        and roles[target.ADMIN]["superuser"] is False
        and record["future_trial_database_present"] is False
        and record["future_trial_roles_present"] == [], "boundary_catalog_unreviewed")
    exceptions = []
    for name in sorted(PROVIDER_DATABASES):
        db = databases[name]
        grants = [g for g in catalog["database_acls"] if g["object_oid"] == db["oid"] and g["grantee_oid"] == 0]
        require(db["owner_oid"] == roles["azuresu"]["oid"] and db["allow_connections"] is True
            and {g["privilege"] for g in grants} == {"CONNECT", "TEMPORARY"}
            and len(grants) == 2 and all(g["grantor_oid"] == db["owner_oid"] and g["grantable"] is False for g in grants),
            "boundary_catalog_unreviewed")
        exceptions.append(dict(database=name, owner="azuresu", public_privileges=["CONNECT", "TEMPORARY"],
            actual_connection_denial_verified=False, sensitive_access_denial_verified=False))
    return dict(status="approved_catalog_policy_reviewed_behavior_pending", policy=POLICY,
        host=target.HOST, database=target.DATABASE, catalog_sha256=record["catalog_sha256"],
        exception_databases=exceptions, customer_databases_requiring_public_revoke=sorted(CUSTOMER_DATABASES),
        public_schema_revoke_requires_admin_before_set_role=True,
        installation_dispatched=False, fresh_login_verified=False, credential_isolation_verified=False,
        source_rows_read=0, source_rows_written=0, step_2_complete=False)


def customer_database_revokes(record, scope=Scope()):
    """Trusted installer plan only; never REVOKE on Microsoft-owned databases."""
    review_catalog(record, scope)
    return tuple(sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(sql.Identifier(name))
        for name in sorted(CUSTOMER_DATABASES))


def public_schema_revoke(connection_identity, scope=Scope()):
    """Run only in the new database AS ADMIN before SET ROLE schema owner.

    A controller must obtain this exact identity on its own trusted connection;
    a caller-supplied dict is not itself proof of database authentication.
    """
    checked_scope(scope)
    require(type(connection_identity) is dict and connection_identity == {
        "host": target.HOST, "database": target.DATABASE, "session_user": target.ADMIN,
        "current_user": target.ADMIN, "public_schema_owner": "azure_pg_admin",
    }, "boundary_installer_mismatch")
    return "REVOKE ALL ON SCHEMA public FROM PUBLIC"


def validate_database_privileges(rows, scope=Scope()):
    """Complete named effective-role metadata, not behavioral SQL acceptance.

    Provider defaults are allowed for the two exact databases only. Both roles
    remain NOINHERIT and the capability has no CONNECT on the target database.
    Normal PostgreSQL template0 is excluded because datallowconn is false.
    """
    checked_scope(scope)
    fields = {"role", "database", "connect", "create", "temporary"}
    databases = PROVIDER_DATABASES | CUSTOMER_DATABASES | {target.DATABASE}
    require(type(rows) is list and len(rows) == len(ROLES) * len(databases)
        and all(type(r) is dict and set(r) == fields
            and type(r["role"]) is str and type(r["database"]) is str
            and all(type(r[k]) is bool for k in ("connect", "create", "temporary")) for r in rows),
        "boundary_privilege_inventory_invalid")
    require({(r["role"], r["database"]) for r in rows} == {(role, db) for role in ROLES for db in databases},
        "boundary_privilege_inventory_invalid")
    for row in rows:
        provider = row["database"] in PROVIDER_DATABASES
        require(row["create"] is False
            and row["temporary"] is provider
            and row["connect"] is (provider or row["database"] == target.DATABASE and row["role"] == target.LOGIN),
            "boundary_privilege_exceeded")
    return dict(status="database_metadata_policy_passed_behavior_pending", policy=POLICY,
        fresh_login_verified=False, source_data_isolation_verified=False, step_2_complete=False)


def validate_provider_behavior(probes, scope=Scope()):
    """A narrow initial behavioral gate; generic authentication/network failures
    never prove authorization denial. A connected provider database requires a
    later exact object/monitoring-data review; it cannot silently pass this gate.
    Source protection is not established merely by provider-name recognition.
    """
    checked_scope(scope)
    fields = {"host", "database", "login", "credential_target_login_verified",
        "outcome", "sqlstate", "fresh_connection", "source_rows_read", "source_rows_written"}
    require(type(probes) is list and len(probes) == 2
        and all(type(p) is dict and set(p) == fields
            and all(type(p[k]) is str for k in ("host", "database", "login", "outcome", "sqlstate"))
            for p in probes), "boundary_behavior_unaccepted")
    require({p["database"] for p in probes} == PROVIDER_DATABASES, "boundary_behavior_unaccepted")
    for probe in probes:
        require(probe["host"] == target.HOST and probe["login"] == target.LOGIN
            and probe["credential_target_login_verified"] is True and probe["fresh_connection"] is True
            and probe["outcome"] == "authorization_denied" and probe["sqlstate"] in ("42501", "28000")
            and all(type(probe[k]) is int and probe[k] == 0 for k in ("source_rows_read", "source_rows_written")),
            "boundary_behavior_unaccepted")
    return dict(status="provider_connection_denials_validated_receipts_required", policy=POLICY,
        protected_receipts_verified=False, authenticated_provenance_verified=False, step_2_complete=False)
