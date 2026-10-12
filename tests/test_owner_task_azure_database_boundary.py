"""Offline policy tests. No live database, credential or Azure acceptance."""
import ast
import copy
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import owner_task_azure_database_boundary as policy
import inspect_owner_task_isolated_provider as collector
from ibkr_etl import owner_codex_isolated_target as target


def record():
    # Minimal selected fixture; the real collector validation is independently
    # tested elsewhere. Tests mock ONLY that validator, never live acceptance.
    return dict(catalog_sha256=policy.APPROVED_CATALOG_SHA256,
        future_trial_database_present=False, future_trial_roles_present=[], catalog=dict(
        roles=[dict(name="azuresu", oid=10, superuser=True), dict(name=target.ADMIN, oid=42, superuser=False)],
        databases=[dict(name=name, oid=oid, owner_oid=10 if name in policy.PROVIDER_DATABASES else 43,
            allow_connections=name != "template0") for oid, name in enumerate(
                ("postgres", "template0", "template1", "azure_maintenance", "azure_sys"), 1)],
        database_acls=[dict(object_oid=oid, grantee_oid=0, grantor_oid=10,
            privilege=privilege, grantable=False) for oid in (4, 5) for privilege in ("CONNECT", "TEMPORARY")]))


def rows():
    return [dict(role=role, database=db, connect=db in policy.PROVIDER_DATABASES or db == target.DATABASE and role == target.LOGIN,
        create=False, temporary=db in policy.PROVIDER_DATABASES)
        for role in sorted(policy.ROLES) for db in sorted(policy.PROVIDER_DATABASES | policy.CUSTOMER_DATABASES | {target.DATABASE})]


def probes():
    return [dict(host=target.HOST, database=db, login=target.LOGIN,
        credential_target_login_verified=True, outcome="authorization_denied", sqlstate="42501",
        fresh_connection=True, source_rows_read=0, source_rows_written=0) for db in sorted(policy.PROVIDER_DATABASES)]


class AzureBoundaryTests(unittest.TestCase):
    def assert_denied(self, call, *args):
        with self.assertRaises(policy.BoundaryError): call(*args)

    def test_import_inert_and_old_policy_unmodified(self):
        source = Path(policy.__file__).read_text()
        self.assertNotRegex(source, r"psycopg\.connect|os\.environ|AzureCliCredential|subprocess\.|requests\.")
        self.assertFalse(any(isinstance(n, ast.If) for n in ast.parse(source).body))
        self.assertIn("system_database_privilege_exception_allowed=False", Path(collector.__file__).read_text())

    def test_exact_scope_only(self):
        for name in vars(policy.Scope()):
            with self.subTest(name=name):
                with self.assertRaises(policy.BoundaryError): policy.Scope(**{name: "other"})
        scope = policy.Scope(); object.__setattr__(scope, "host", "production")
        self.assert_denied(policy.checked_scope, scope)
        self.assert_denied(policy.checked_scope, dict(host=target.HOST))
        scope = policy.Scope(); object.__setattr__(scope, "extra", "allowed")
        self.assert_denied(policy.checked_scope, scope)

    def test_pinned_review_not_authentication_and_preserves_record(self):
        value = record(); before = copy.deepcopy(value)
        with patch.object(collector, "validate_record", return_value=value) as validator:
            result = policy.review_catalog(value)
        validator.assert_called_once_with(value)
        self.assertEqual(before, value)
        self.assertEqual(sorted(policy.CUSTOMER_DATABASES), result["customer_databases_requiring_public_revoke"])
        self.assertFalse(result["step_2_complete"])
        self.assertFalse(result["fresh_login_verified"])
        self.assertFalse(result["credential_isolation_verified"])

    def test_real_collector_not_bypassed_by_minimal_fixture(self):
        self.assert_denied(policy.review_catalog, record())

    def test_catalog_drift_fails(self):
        for change in (lambda r:r.update(catalog_sha256="a" * 64),
            lambda r:r.update(future_trial_database_present=True),
            lambda r:r.update(future_trial_roles_present=[target.LOGIN]),
            lambda r:r["catalog"]["databases"].append(dict(name="business",oid=100,owner_oid=10,allow_connections=True)),
            lambda r:r["catalog"]["database_acls"][0].update(grantable=True),
            lambda r:r["catalog"]["database_acls"][0].update(grantor_oid=42),
            lambda r:r["catalog"]["database_acls"][0].update(privilege="CREATE"),
            lambda r:r["catalog"]["databases"][3].update(owner_oid=42),
            lambda r:r["catalog"]["roles"][1].update(superuser=True)):
            value=record(); change(value)
            with patch.object(collector,"validate_record",return_value=value):
                self.assert_denied(policy.review_catalog,value)

    def test_customer_revoke_plan_never_provider(self):
        value=record()
        with patch.object(collector,"validate_record",return_value=value):
            statements=policy.customer_database_revokes(value)
        self.assertEqual(['REVOKE ALL ON DATABASE "postgres" FROM PUBLIC',
            'REVOKE ALL ON DATABASE "template1" FROM PUBLIC'],[s.as_string(None) for s in statements])

    def test_public_schema_admin_and_exact_database_required(self):
        identity=dict(host=target.HOST,database=target.DATABASE,session_user=target.ADMIN,
            current_user=target.ADMIN,public_schema_owner="azure_pg_admin")
        self.assertEqual("REVOKE ALL ON SCHEMA public FROM PUBLIC",policy.public_schema_revoke(identity))
        for key in identity:
            bad=dict(identity);bad[key]="other";self.assert_denied(policy.public_schema_revoke,bad)
        self.assert_denied(policy.public_schema_revoke,dict(identity,extra=True))

    def test_effective_metadata_passes_without_live_claim(self):
        self.assertFalse(policy.validate_database_privileges(rows())["step_2_complete"])

    def test_customer_privileges_cannot_be_exempted(self):
        for db in ("postgres","template1",target.DATABASE):
            for key in ("create","temporary","connect"):
                value=rows();row=next(r for r in value if r["database"]==db and r["role"]==target.CAPABILITY)
                row[key]=True;self.assert_denied(policy.validate_database_privileges,value)

    def test_provider_create_not_exempted(self):
        for db in policy.PROVIDER_DATABASES:
            value=rows();next(r for r in value if r["database"]==db)["create"]=True
            self.assert_denied(policy.validate_database_privileges,value)

    def test_metadata_inventory_complete_unique_and_strict_types(self):
        for bad in (rows()[:-1],rows()+[rows()[0]],rows()[:-1]+[rows()[0]]):
            self.assert_denied(policy.validate_database_privileges,bad)
        for key,bad in (("role",True),("database","azure_sys_extra"),("connect",1),("create",0),("temporary","true")):
            value=rows();value[0][key]=bad;self.assert_denied(policy.validate_database_privileges,value)

    def test_provider_defaults_cannot_disappear_unreviewed(self):
        for key in ("connect","temporary"):
            value=rows();next(r for r in value if r["database"]=="azure_sys")[key]=False
            self.assert_denied(policy.validate_database_privileges,value)

    def test_denial_protocol_still_needs_protected_authentication(self):
        result=policy.validate_provider_behavior(probes())
        self.assertFalse(result["protected_receipts_verified"])
        self.assertFalse(result["authenticated_provenance_verified"])
        self.assertFalse(result["step_2_complete"])

    def test_connected_provider_not_silently_accepted(self):
        value=probes();value[0].update(outcome="connected",sqlstate=None)
        self.assert_denied(policy.validate_provider_behavior,value)

    def test_wrong_password_network_or_tls_failure_not_authorization_proof(self):
        for state in ("28P01","08006","08001","3D000",None):
            value=probes();value[0]["sqlstate"]=state;self.assert_denied(policy.validate_provider_behavior,value)

    def test_behavior_exact_scope_identity_provenance_and_no_source_access(self):
        for key,bad in (("host","production"),("database","postgres"),("login",target.ADMIN),
            ("credential_target_login_verified",1),("fresh_connection",False),
            ("source_rows_read",1),("source_rows_written",False)):
            value=probes();value[0][key]=bad;self.assert_denied(policy.validate_provider_behavior,value)
        self.assert_denied(policy.validate_provider_behavior,probes()[:1])
        self.assert_denied(policy.validate_provider_behavior,[probes()[0],probes()[0]])
        value=probes();value[0]["database"]=[]
        self.assert_denied(policy.validate_provider_behavior,value)


if __name__ == "__main__": unittest.main()
