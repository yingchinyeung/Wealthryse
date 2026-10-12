"""Synthetic read-only catalog contracts; NOT live PostgreSQL qualification."""
import ast
import copy
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import inspect_owner_task_isolated_provider as collector
from ibkr_etl import owner_codex_isolated_target as registry
from ibkr_etl import owner_codex_catalog_transport as transport


def role(oid, name, *, superuser=False):
    return dict(oid=oid, name=name, can_login=True, inherit=True, superuser=superuser,
        create_database=superuser, create_role=superuser, replication=False, bypass_rls=False,
        connection_limit=-1, valid_until=None, configuration_sha256=None, marker_sha256=None)


def acl(oid, privilege, grantee=0):
    return dict(object_oid=oid, grantee_oid=grantee, grantor_oid=11, privilege=privilege, grantable=False)


def routine(oid, schema=21, kind="f"):
    return dict(oid=oid, name="provider_function_" + str(oid), owner_oid=11, schema_oid=schema,
        kind=kind, identity_arguments="argument pg_catalog.text", language_oid=70, language="plpgsql",
        language_trusted=True, security_definer=True, leakproof=False, volatility="v", parallel="u",
        configuration_present=True, configuration_count=1, configuration_sha256="c" * 64,
        body_sha256="b" * 64, binary_sha256=None,
        definition_sha256=None if kind == "a" else "d" * 64, acl_sha256=None)


def catalog():
    return dict(databases=[dict(oid=oid, name=name, owner_oid=11 if oid > 30 else 10,
        allow_connections=name != "template0", is_template=name.startswith("template"),
        connection_limit=-1, marker_sha256=None) for oid, name in
        enumerate(("postgres", "template0", "template1", "azure_maintenance", "azure_sys"), 30)],
        database_acls=[acl(n, "CONNECT") for n in range(30, 35)],
        roles=[role(10, registry.ADMIN), role(11, "azure_superuser", superuser=True), role(12, "pg_database_owner")],
        memberships=[dict(member_oid=10, role_oid=11, grantor_oid=11, admin_option=False, inherit_option=True, set_option=True)],
        schemas=[dict(oid=20, name="pg_catalog", owner_oid=11), dict(oid=21, name="public", owner_oid=12),
                 dict(oid=22, name="unexpected_provider_schema", owner_oid=11)],
        schema_acls=[acl(21, "USAGE")],
        default_acls=[dict(oid=80, owner_oid=10, schema_oid=0, object_type="r", acl_sha256="a" * 64)],
        default_acl_grants=[acl(80, "SELECT")],
        extensions=[dict(oid=40, name="plpgsql", owner_oid=11, schema_oid=20, version="1.0", relocatable=False)],
        relations=[dict(oid=50, name="provider_view", owner_oid=11, schema_oid=22, kind="v", persistence="p",
                        row_security=False, force_row_security=False, acl_sha256=None)],
        relation_acls=[acl(50, "SELECT")],
        routines=[routine(60), routine(61, 20), routine(62, 20, "a")],
        routine_acls=[acl(n, "EXECUTE") for n in range(60, 63)])


class Raw:
    def __init__(self):
        self.autocommit = True
        self.info = SimpleNamespace(host=registry.HOST, dbname=registry.MANAGEMENT_DATABASE,
            user=registry.ADMIN, transaction_status=0, backend_pid=100)
        self.pgconn = SimpleNamespace(ssl_in_use=True)
        self.path = dict(search_path="pg_catalog", temporary_schema=0)
        self.identity = dict(database="postgres", role=registry.ADMIN, login=registry.ADMIN,
            version="160015", readonly="on", backend_pid=100)
        self.catalog = catalog()
        self.calls = []
        self.on_execute = None

    def execute(self, text):
        self.calls.append(text)
        if self.on_execute is not None:
            self.on_execute(self, text)
        if text == collector.PATH_SQL: value = copy.deepcopy(self.path)
        elif text == collector.IDENTITY_SQL: value = copy.deepcopy(self.identity)
        else:
            keys = [k for k, spec in collector.SPECS.items() if spec[3] == text]
            if len(keys) != 1: raise ValueError("unregistered synthetic SELECT")
            value = copy.deepcopy(self.catalog[keys[0]])
        return SimpleNamespace(fetchone=lambda: value, fetchall=lambda: value)


class IsolatedProviderTests(unittest.TestCase):
    def test_observation_complete_bounded_and_never_authorizes(self):
        raw = Raw()
        value = collector.inspect_provider(raw)
        self.assertEqual("observed", value["status"])
        self.assertEqual(set(collector.SPECS), set(value["catalog"]))
        self.assertEqual({k: len(v) for k, v in raw.catalog.items()}, value["catalog_counts"])
        self.assertTrue(value["review_required"])
        for name in ("setup_eligible", "restricted_setup_authorized", "provider_semantics_verified",
            "system_database_privilege_exception_allowed", "fresh_restricted_login_verified",
            "authenticated_owner_verified", "model_credential_isolation_verified", "protected_retention_verified",
            "catalog_consistent_snapshot_verified"):
            self.assertIs(value[name], False)
        self.assertEqual([], value["unrecognized_database_names"])
        self.assertEqual([62], value["definition_unavailable_routine_oids"])
        self.assertEqual(raw.calls[0], collector.PATH_SQL)
        self.assertNotIn("::", raw.calls[0])
        self.assertEqual(4 + len(collector.SPECS), len(raw.calls))
        self.assertLessEqual(len(collector.canonical(value)), collector.MAX_RECORD_BYTES)

    def test_only_inert_readonly_qualified_catalog_source(self):
        source = Path(collector.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(n, ast.If) for n in tree.body))
        self.assertNotRegex(source, r"os\.environ|psycopg\.connect|requests\.|subprocess\.|AzureCliCredential")
        raw = Raw()
        collector.inspect_provider(raw)
        self.assertTrue(all(q.startswith("SELECT ") for q in raw.calls))
        for query in raw.calls:
            self.assertNotRegex(query, r"\b(?:INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|GRANT|REVOKE|SET|set_config)\b")
            self.assertNotIn("rolpassword", query)
        self.assertTrue(all(repr(spec[0] + 1) in spec[3] for spec in collector.SPECS.values()))

    def test_exact_connection_old_host_user_database_tls_rejected_before_queries(self):
        cases = (("host", "ibkrv2stg-pg-auhecwc4itaoo.postgres.database.azure.com"),
                 ("host", registry.HOST + ".evil"), ("user", "other_admin"),
                 ("dbname", registry.DATABASE), ("user", True))
        for key, bad in cases:
            raw = Raw(); setattr(raw.info, key, bad)
            with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)
            self.assertEqual([], raw.calls)
        for bad in (False, 1, "True"):
            raw = Raw(); raw.pgconn.ssl_in_use = bad
            with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)
            self.assertEqual([], raw.calls)

    def test_dirty_transaction_nonboolean_autocommit_and_idle_alias_rejected(self):
        for key, bad in (("autocommit", False), ("autocommit", 1), ("transaction_status", 2),
                         ("transaction_status", False), ("transaction_status", 0.0)):
            raw = Raw()
            setattr(raw if key == "autocommit" else raw.info, key, bad)
            with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)
            self.assertEqual([], raw.calls)

    def test_temp_decoy_and_unsafe_path_stop_before_casts(self):
        for path in (dict(search_path="public, pg_catalog", temporary_schema=0),
                     dict(search_path="pg_catalog", temporary_schema=9000),
                     dict(search_path="pg_catalog", temporary_schema=False),
                     dict(search_path="pg_catalog", temporary_schema=0, extra=True)):
            raw = Raw(); raw.path = path
            with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)
            self.assertEqual([collector.PATH_SQL], raw.calls)

    def test_identity_readonly_major_version_backend_and_session_mismatch(self):
        for key, bad in (("role", "other"), ("login", "other"), ("database", "template1"),
                         ("readonly", "off"), ("version", "170000"), ("version", 160015),
                         ("backend_pid", True), ("backend_pid", 101)):
            raw = Raw(); raw.identity[key] = bad
            with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)
            self.assertEqual(2, len(raw.calls))

    def test_nonboolean_catalog_fields_oid_and_limit_aliases_rejected(self):
        for section, field in (("databases", "allow_connections"), ("roles", "can_login"),
                               ("schemas", "owner_oid"), ("roles", "connection_limit"),
                               ("memberships", "admin_option"), ("extensions", "relocatable"),
                               ("routines", "security_definer"), ("routines", "language_trusted"),
                               ("routine_acls", "grantable")):
            raw = Raw(); raw.catalog[section][0][field] = 1 if field not in {"owner_oid", "connection_limit"} else True
            with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)

    def test_complete_query_max_plus_one_overflow_rejected_without_sampling(self):
        for section, spec in collector.SPECS.items():
            raw = Raw()
            row = raw.catalog[section][0]
            raw.catalog[section] = [row] * (spec[0] + 1)
            with self.assertRaises(collector.ProviderObservationError) as error: collector.inspect_provider(raw)
            self.assertEqual("provider_catalog_overflow", error.exception.code)

    def test_invalid_references_duplicates_sort_and_unknown_fields_rejected(self):
        changes = (("roles", lambda rows: rows.reverse()),
            ("roles", lambda rows: rows.append(copy.deepcopy(rows[0]))),
            ("schemas", lambda rows: rows[0].update(owner_oid=999)),
            ("routine_acls", lambda rows: rows[0].update(grantee_oid=999)),
            ("routine_acls", lambda rows: rows[0].update(object_oid=999)),
            ("memberships", lambda rows: rows[0].update(role_oid=999)),
            ("default_acls", lambda rows: rows[0].update(schema_oid=999)),
            ("routines", lambda rows: rows[0].update(raw_definition="do not retain")))
        for section, change in changes:
            raw = Raw(); change(raw.catalog[section])
            with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)

    def test_provider_drift_unknown_schema_role_extension_function_not_whitelisted(self):
        baseline = collector.inspect_provider(Raw())
        changes = (("roles", lambda rows: rows[1].update(bypass_rls=True)),
            ("schemas", lambda rows: rows[2].update(owner_oid=10)),
            ("extensions", lambda rows: rows[0].update(version="unexpected-next")),
            ("routines", lambda rows: rows[1].update(body_sha256="e" * 64, configuration_sha256="e" * 64)),
            ("routine_acls", lambda rows: rows[0].update(grantable=True)))
        for section, change in changes:
            raw = Raw(); change(raw.catalog[section])
            value = collector.inspect_provider(raw)
            self.assertNotEqual(baseline["catalog_sha256"], value["catalog_sha256"])
            self.assertIs(value["review_required"], True)
            self.assertIs(value["provider_semantics_verified"], False)
            self.assertIs(value["setup_eligible"], False)
        self.assertNotIn("WHERE", collector.SPECS["routines"][3])

    def test_unknown_nondefault_database_and_future_objects_need_review(self):
        raw = Raw()
        raw.catalog["databases"].append(dict(oid=35, name=registry.DATABASE, owner_oid=10,
            allow_connections=True, is_template=False, connection_limit=-1, marker_sha256=None))
        raw.catalog["roles"].append(role(13, registry.LOGIN))
        value = collector.inspect_provider(raw)
        self.assertEqual([registry.DATABASE], value["unrecognized_database_names"])
        self.assertTrue(value["future_trial_database_present"])
        self.assertEqual([registry.LOGIN], value["future_trial_roles_present"])
        self.assertIs(value["restricted_setup_authorized"], False)
        self.assertIs(value["system_database_privilege_exception_allowed"], False)

    def test_public_and_provider_routine_identity_acl_metadata_explicit(self):
        value = collector.inspect_provider(Raw())
        self.assertEqual({20, 21}, {r["schema_oid"] for r in value["catalog"]["routines"]})
        self.assertTrue(all(r["owner_oid"] == 11 and r["language"] == "plpgsql" for r in value["catalog"]["routines"]))
        self.assertTrue(all(r["privilege"] == "EXECUTE" and r["grantee_oid"] == 0 for r in value["catalog"]["routine_acls"]))
        self.assertEqual(0, value["public_acl_grantee_oid"])
        self.assertTrue(all(r["body_sha256"] == "b" * 64 and "raw_definition" not in r for r in value["catalog"]["routines"]))

    def test_fixed_result_boolean_numeric_aliases_and_digest_bindings(self):
        good = collector.inspect_provider(Raw())
        for key, bad in (("source_rows_read", False), ("setup_eligible", 0), ("tls_verified", 1),
                         ("catalog_sha256", "a" * 64), ("unrecognized_database_names", ["postgres"]),
                         ("catalog_counts", {**good["catalog_counts"], "roles": True}),
                         ("host", "old"), ("future_trial_database_present", 0),
                         ("definition_unavailable_routine_oids", []), ("server_version_num", "170000")):
            value = copy.deepcopy(good); value[key] = bad
            with self.assertRaises(collector.ProviderObservationError): collector.validate_record(value)

    def test_canonical_complete_record_bound_no_truncation(self):
        value = collector.inspect_provider(Raw())
        size = len(collector.canonical(value))
        with patch.object(collector, "MAX_RECORD_BYTES", size): collector.validate_record(value)
        with patch.object(collector, "MAX_RECORD_BYTES", size - 1):
            with self.assertRaises(collector.ProviderObservationError) as error: collector.validate_record(value)
            self.assertEqual("provider_record_oversized", error.exception.code)

    def test_names_arguments_configuration_and_hashes_bounded(self):
        for field, bad in (("identity_arguments", "x" * 4097), ("configuration_count", 1025),
                           ("configuration_count", True), ("configuration_present", 1),
                           ("configuration_sha256", "custom_guc=synthetic-secret"), ("body_sha256", "raw body"),
                           ("name", "unsafe\nname"), ("definition_sha256", True)):
            raw = Raw(); raw.catalog["routines"][0][field] = bad
            with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)

    def test_configuration_is_fingerprint_only_and_null_metadata_consistent(self):
        query = collector.SPECS["routines"][3]
        self.assertNotIn("p.proconfig AS configuration", query)
        self.assertIn("p.proconfig::pg_catalog.text", query)
        self.assertIn("AS configuration_sha256", query)
        raw = Raw()
        raw.catalog["routines"][0].update(configuration_present=False, configuration_count=0,
                                           configuration_sha256=None)
        value = collector.inspect_provider(raw)
        self.assertNotIn("configuration", value["catalog"]["routines"][0])
        for change in (dict(configuration_present=False), dict(configuration_sha256=None),
                       dict(configuration_present=False, configuration_sha256=None)):
            raw = Raw(); raw.catalog["routines"][0].update(change)
            with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)
        raw = Raw(); raw.catalog["routines"][0]["configuration"] = ["custom_guc=synthetic-secret"]
        with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)

    def test_full_thousand_routine_scale_is_measured_not_sampled(self):
        # Synthetic capacity exercise, NOT a measured PostgreSQL/provider catalog.
        # Complete catalog is above a single 2 MiB receipt but below codec's 4 MiB.
        raw = Raw()
        raw.catalog["routines"] = [routine(1000 + n, 20) for n in range(3250)]
        raw.catalog["routine_acls"] = [acl(1000 + n, "EXECUTE", grantee)
            for n in range(3250) for grantee in (0, 11)]
        value = collector.inspect_provider(raw)
        self.assertEqual(3250, value["catalog_counts"]["routines"])
        self.assertEqual(6500, value["catalog_counts"]["routine_acls"])
        self.assertGreater(len(collector.canonical(value)), 2 * 1024 * 1024)
        self.assertLessEqual(len(collector.canonical(value)), collector.MAX_RECORD_BYTES)
        self.assertEqual(transport.MAX_RECORD_BYTES, collector.MAX_RECORD_BYTES)
        self.assertEqual(value, collector.validate_record(value))
        self.assertIs(value["protected_retention_verified"], False)
        scope = transport.Binding("provider", registry.PROFILE_KEY, registry.JOB_ID,
            "9f9d44a5-3082-4932-8362-34750b6e3702", "a" * 64)
        encoded = transport.encode_record(value, scope)
        self.assertEqual(value, transport.decode_record(encoded, scope))  # Synthetic codec, not Azure transport.

    def test_over_four_mib_within_every_row_and_field_bound_still_rejected(self):
        raw = Raw()
        raw.catalog["routines"] = [dict(routine(1000 + n, 20), identity_arguments="argument " + "x" * 1024)
            for n in range(collector.SPECS["routines"][0])]
        raw.catalog["routine_acls"] = [acl(1000 + n, "EXECUTE", grantee)
            for n in range(len(raw.catalog["routines"])) for grantee in (0, 11)]
        self.assertTrue(all(len(raw.catalog[key]) <= spec[0] for key, spec in collector.SPECS.items()))
        self.assertEqual(raw.catalog, collector._validate_catalog(raw.catalog))
        self.assertGreater(len(collector.canonical(raw.catalog)), transport.MAX_RECORD_BYTES)
        with self.assertRaises(collector.ProviderObservationError) as error:
            collector.inspect_provider(raw)
        self.assertEqual("provider_record_oversized", error.exception.code)
        self.assertEqual(4 + len(collector.SPECS), len(raw.calls))  # Complete query set, no sampling.

    def test_final_catalog_context_drift_withholds_record(self):
        for change in (lambda raw: raw.path.update(temporary_schema=9000),
                       lambda raw: raw.identity.update(role="other"),
                       lambda raw: setattr(raw.info, "transaction_status", 2)):
            raw = Raw()
            def on_execute(raw, text):
                if text == collector.PATH_SQL and len(raw.calls) > 2: change(raw)
            raw.on_execute = on_execute
            with self.assertRaises(collector.ProviderObservationError): collector.inspect_provider(raw)

    def test_provider_error_text_never_returned(self):
        raw = Raw()
        def fail(raw, text): raise RuntimeError("synthetic credentials must not escape")
        raw.on_execute = fail
        with self.assertRaises(collector.ProviderObservationError) as error: collector.inspect_provider(raw)
        self.assertEqual("provider_observation_failed", str(error.exception))
        self.assertNotIn("credentials", str(error.exception))


if __name__ == "__main__":
    unittest.main()
