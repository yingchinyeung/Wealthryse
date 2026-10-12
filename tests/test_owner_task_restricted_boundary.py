"""Offline catalog and one-shot diagnostic guards; no live/cloud acceptance."""
import ast
from contextlib import contextmanager, redirect_stdout
import copy
import io
import json
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch
from contextlib import ExitStack

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import inspect_owner_task_restricted_boundary as collector
import qualify_owner_task_restricted_boundary as driver
from ibkr_etl import owner_codex_permissions as permissions


class Raw:
    def __init__(self):
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=0, host=collector.HOST, dbname="postgres", user="SYNTH_INSTALLER")
        self.pgconn = SimpleNamespace(ssl_in_use=True)
        self.identity = dict(database="postgres", role="SYNTH_INSTALLER", login="SYNTH_INSTALLER", version="160015",
            readonly="on", search_path="pg_catalog", temporary_schema=0)
        self.public = []
        self.database = None
        self.roles = []
        self.calls = []

    def execute(self, text, args=()):
        self.calls.append((text, args))
        if "transaction_read_only" in text: value = self.identity
        elif "SELECT DISTINCT" in text: value = self.public
        elif "SELECT datname" in text: value = self.database
        else: value = self.roles
        return SimpleNamespace(fetchone=lambda: value, fetchall=lambda: value)


class EvidenceFile:
    def __init__(self, folder, name): self.folder, self.name = folder, name
    def exists(self): return self.name in self.folder.values
    def read_text(self, **kwargs): return json.dumps(self.folder.values[self.name])


class EvidenceFolder:
    def __init__(self): self.values = {}
    def __truediv__(self, name): return EvidenceFile(self, name)
    def mkdir(self, **kwargs): pass


def record():
    return collector.inspect_boundary(Raw())


def job():
    identity = driver.deploy.BASE + "Microsoft.ManagedIdentity/userAssignedIdentities/ibkrv2stg-ingestion-identity"
    return dict(identity=dict(type="UserAssigned", userAssignedIdentities={identity: {}}), properties=dict(
        configuration=dict(triggerType="Manual", replicaRetryLimit=0,
            manualTriggerConfig=dict(parallelism=1, replicaCompletionCount=1),
            secrets=[dict(name="database-admin-url", keyVaultUrl="https://ibkrv2stg-auhecwc4itaoo.vault.azure.net/secrets/database-admin-url", identity=identity)]),
        template=dict(containers=[dict(name="postgres-bootstrap", image="ibkrv2stgauhecwc4itaoo.azurecr.io/ibkr-platform@sha256:276a4783bf4ddf437bd5d449e08df39729fb43f449975082af80fd4c13e1142c", command=["python"], args=["original"], env=[], resources={})])))


class RestrictedBoundaryTests(unittest.TestCase):
    def test_registry_matches_restricted_permission_specification(self):
        self.assertEqual((collector.HOST, collector.DATABASE, set(collector.ROLES)),
            (permissions.HOST, permissions.DATABASE, {permissions.SCHEMA_OWNER, permissions.CAPABILITY, permissions.LOGIN}))

    def test_collector_import_inert_and_only_fixed_catalog_selects(self):
        source = Path(collector.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(n, ast.If) for n in tree.body))
        self.assertNotRegex(source, r"psycopg\.connect|os\.environ|subprocess\.|requests\.|CREATE ROLE|ALTER ROLE|DROP DATABASE|REVOKE")
        raw = Raw()
        result = collector.inspect_boundary(raw)
        self.assertEqual(len(raw.calls), 4)
        self.assertTrue(all(q.startswith("SELECT") for q, _ in raw.calls))
        self.assertIn("pg_catalog.acldefault('d'", raw.calls[1][0])
        self.assertEqual(raw.calls[1][1], (collector.DATABASE,))
        self.assertEqual(raw.calls[2][1], (collector.DATABASE,))
        self.assertEqual(raw.calls[3][1], (list(collector.ROLES),))
        self.assertIs(result["database_boundary_eligible"], True)
        self.assertIs(result["restricted_setup_authorized"], False)

    def test_public_connect_is_successful_observation_but_ineligible_for_setup(self):
        raw = Raw()
        raw.public = [{"datname": "ibkr_staging"}, {"datname": "postgres"}]
        result = collector.inspect_boundary(raw)
        self.assertEqual(result["status"], "observed")
        self.assertEqual(result["non_target_public_connect_count"], 2)
        self.assertEqual(result["reason"], "non_target_public_database_connect")
        self.assertIs(result["database_boundary_eligible"], False)
        self.assertIs(result["restricted_setup_authorized"], False)
        self.assertTrue(all(q.startswith("SELECT") for q, _ in raw.calls))

    def test_existing_exact_role_or_database_blocks_without_mutation(self):
        for db, roles in (({"datname": collector.DATABASE}, []), (None, [{"rolname": collector.ROLES[0]}])):
            raw = Raw()
            raw.database, raw.roles = db, roles
            result = collector.inspect_boundary(raw)
            self.assertIs(result["database_boundary_eligible"], False)
            self.assertEqual(result["reason"], "registered_scope_exists")

    def test_bad_connection_identity_readonly_and_temporary_scope_fail_closed(self):
        changes = (lambda r: setattr(r, "autocommit", False), lambda r: setattr(r.info, "transaction_status", 2),
            lambda r: setattr(r.info, "host", "other.example"), lambda r: setattr(r.info, "dbname", "ibkr_staging"),
            lambda r: setattr(r.pgconn, "ssl_in_use", 1), lambda r: r.identity.update(readonly="off"),
            lambda r: r.identity.update(version="150010"), lambda r: r.identity.update(version=160015),
            lambda r: r.identity.update(login="OTHER"), lambda r: r.identity.update(role=collector.ROLES[0]),
            lambda r: r.identity.update(temporary_schema=True), lambda r: r.identity.update(temporary_schema=1),
            lambda r: r.identity.update(search_path="public"), lambda r: r.identity.update(extra="unknown"))
        for change in changes:
            raw = Raw()
            change(raw)
            with self.subTest(change=change), self.assertRaises(collector.BoundaryError):
                collector.inspect_boundary(raw)

    def test_unknown_duplicate_or_malformed_catalog_rows_fail_closed(self):
        for field, value in (("public", None), ("public", [{"datname": "postgres", "extra": True}]),
            ("public", [{"datname": "postgres"}, {"datname": "postgres"}]),
            ("database", {"datname": "ibkr_staging"}), ("roles", None),
            ("roles", [{"rolname": "unknown"}]), ("roles", [{"rolname": collector.ROLES[0]}] * 2)):
            raw = Raw()
            setattr(raw, field, value)
            with self.subTest(field=field, value=value), self.assertRaises(collector.BoundaryError):
                collector.inspect_boundary(raw)

    def test_record_strict_fixed_flags_counts_scope_and_reason(self):
        for key, wrong in (("status", "passed"), ("readonly_verified", 1), ("postgres_major", True),
            ("restricted_setup_authorized", True), ("source_rows_read", False), ("grants_changed", 1),
            ("trial_roles_absent", 1), ("database_boundary_eligible", False), ("reason", "other"),
            ("non_target_public_connect_count", True), ("non_target_public_connect_databases", [collector.DATABASE]),
            ("non_target_public_connect_databases", ["not safe;"]), ("extra", "unknown")):
            changed = dict(record(), **{key: wrong})
            with self.subTest(key=key), self.assertRaises(collector.BoundaryError):
                collector.validate_record(changed)
        for value in (None, [], {}, True):
            with self.assertRaises(collector.BoundaryError): collector.validate_record(value)

    def test_provider_error_is_fixed_and_never_reflected(self):
        raw = Raw()
        raw.execute = Mock(side_effect=RuntimeError("password=SYNTHETIC_PRIVATE_PAYLOAD"))
        with self.assertRaises(collector.BoundaryError) as error: collector.inspect_boundary(raw)
        self.assertEqual(str(error.exception), "boundary_inspection_failed")
        self.assertEqual(str(collector.BoundaryError("password=SECRET")), "boundary_inspection_failed")

    def test_exact_single_cli_wrapper_only_and_observed_record_validation(self):
        line = collector.PREFIX + json.dumps(record())
        self.assertEqual(driver.log_records([{"Log_s": line}]), [record()])
        self.assertEqual(driver.log_records([{"Log_s": line, "TableName": "PrimaryResult"}]), [record()])
        self.assertEqual(driver.log_records([]), [])
        for rows in ([{"Log_s": line}] * 2, [{"Log_s": line, "TableName": "OtherResult"}],
            [{"Log_s": line, "TableName": True}], [{"Log_s": line, "extra": 0}], [{"Log_s": "other"}], None):
            with self.assertRaises(ValueError): driver.log_records(rows)
        with self.assertRaises(ValueError): driver.observed_record([])
        self.assertEqual(driver.observed_record([record()]), record())

    def test_fixed_failed_worker_record_is_inspectable_but_not_accepted_as_observation(self):
        failed = dict(status="failed", reason="boundary_inspection_failed", automatic_retry=False)
        self.assertEqual(driver.log_records([dict(Log_s=collector.PREFIX + json.dumps(failed))]), [failed])
        with self.assertRaises(ValueError): driver.observed_record([failed])
        failed["automatic_retry"] = 0
        with self.assertRaises(ValueError): driver.log_records([dict(Log_s=collector.PREFIX + json.dumps(failed))])

    def _worker(self, *, connection_error=None):
        raw = Raw()
        raw.public = [{"datname": "postgres"}]
        @contextmanager
        def connect(**kwargs):
            if connection_error: raise connection_error
            yield raw
        output = io.StringIO()
        url = "postgresql://SYNTH_INSTALLER:SYNTHETIC_NOT_A_REAL_SECRET@" + collector.HOST + "/ibkr_staging?sslmode=require"
        with patch.dict("os.environ", {"DATABASE_ADMIN_URL": url}), patch("psycopg.connect", side_effect=connect) as called, redirect_stdout(output):
            with self.assertRaises(SystemExit) as stopped:
                exec(compile(driver.worker_source(), "offline-worker-fixture", "exec"), {"__name__": "offline_worker"})
        lines = output.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertNotIn("SYNTHETIC_NOT_A_REAL_SECRET", lines[0])
        return stopped.exception.code, lines[0], called

    def test_compiled_worker_reads_only_management_catalog_and_masks_credentials(self):
        exit_code, line, called = self._worker()
        self.assertEqual(exit_code, 0)
        result = driver.log_records([dict(Log_s=line)])[0]
        self.assertIs(result["database_boundary_eligible"], False)
        kwargs = called.call_args.kwargs
        self.assertEqual(kwargs["dbname"], "postgres")
        self.assertIs(kwargs["autocommit"], True)
        self.assertIn("default_transaction_read_only=on", kwargs["options"])
        self.assertIn("search_path=pg_catalog", kwargs["options"])

    def test_compiled_worker_error_emits_only_fixed_failure(self):
        exit_code, line, _ = self._worker(connection_error=RuntimeError("password=SYNTHETIC_NOT_A_REAL_SECRET"))
        self.assertEqual(exit_code, 1)
        self.assertEqual(driver.log_records([dict(Log_s=line)]), [dict(status="failed", reason="boundary_inspection_failed", automatic_retry=False)])

    def test_frozen_override_preserves_existing_definition_and_exact_secret_reference(self):
        original = job()
        before = copy.deepcopy(original)
        with patch.object(driver, "source_manifest", return_value={"synthetic.py": "a" * 64}):
            wanted, frozen = driver.frozen_template(original)
        self.assertEqual(original, before)
        self.assertRegex(frozen["payload_sha256"], r"^[0-9a-f]{64}$")
        container = wanted["containers"][0]
        self.assertEqual(container["env"][0], dict(name="DATABASE_ADMIN_URL", secretRef="database-admin-url"))
        self.assertNotIn("value", container["env"][0])
        self.assertEqual(container["resources"], dict(cpu=0.5, memory="1Gi"))
        driver.deploy.verify_containers(wanted["containers"], wanted)

    def test_source_drift_and_unregistered_bootstrap_refuse_payload(self):
        with patch.object(driver, "source_manifest", side_effect=[{"a": "a"}, {"a": "b"}]), self.assertRaises(ValueError):
            driver.frozen_template(job())
        value = job()
        value["properties"]["configuration"]["replicaRetryLimit"] = 1
        with self.assertRaises(ValueError): driver.frozen_template(value)

    def _offline_driver(self, stages, *, dispatch_error=False, protected_error=False, source_drift=False):
        folder, calls, saved = EvidenceFolder(), [], []
        with patch.object(driver, "source_manifest", return_value={"synthetic.py": "a" * 64}):
            wanted, frozen = driver.frozen_template(job())
        execution = driver.deploy.JOB + "-abc1234"
        def request(method, path, **kwargs):
            calls.append((method, path, kwargs))
            if method == "POST":
                if dispatch_error: raise RuntimeError("synthetic uncertain dispatch")
                return {"name": execution}
            if "/executions/" in path:
                return dict(name=execution, properties=dict(status="Succeeded", template=wanted))
            return job()
        def save(value, name, kind):
            saved.append(kind)
            if name in folder.values: raise ValueError("synthetic exclusive reservation")
            # A synthetic receipt is not evidence of protected storage.
            result = dict(value, protected_receipt={"synthetic": True, "kind": kind})
            folder.values[name] = result
            return result
        def protected(value, kind):
            if protected_error and kind == "restricted-boundary-intent":
                raise ValueError("synthetic protected readback failure")
            return {k: v for k, v in value.items() if k != "protected_receipt"}
        source_calls = 0
        def template(value):
            nonlocal source_calls
            source_calls += 1
            if source_drift and source_calls > 2: return wanted, dict(frozen, payload_sha256="b" * 64)
            return wanted, frozen
        with ExitStack() as stack:
            stack.enter_context(patch.object(driver, "FOLDER", folder))
            stack.enter_context(patch.object(driver.deploy, "FOLDER", folder))
            stack.enter_context(patch.object(driver, "frozen_template", side_effect=template))
            stack.enter_context(patch.object(driver, "historical_receipts", return_value=[{"synthetic": True}]))
            stack.enter_context(patch.object(driver.deploy, "request", side_effect=request))
            az = stack.enter_context(patch.object(driver.deploy, "az", side_effect=lambda *args:
                driver.deploy.SUB if args[0] == "account" else [{"Log_s": collector.PREFIX + json.dumps(record()), "TableName": "PrimaryResult"}]))
            stack.enter_context(patch.object(driver.deploy, "save", side_effect=save))
            stack.enter_context(patch.object(driver.deploy, "protected", side_effect=protected))
            stack.enter_context(patch.object(driver.deploy, "baseline", return_value={"job_inventory_complete": True, "other_jobs": dict.fromkeys(map(str, range(34)), "synthetic") }))
            stack.enter_context(patch.object(driver.resume, "assert_bootstrap_idle"))
            stack.enter_context(patch.object(driver.qa, "run_local_checks", return_value={"test_count": 1}))
            output = io.StringIO()
            error = None
            for stage in stages:
                argv = ["offline-driver", stage] + (["--execution", execution] if stage in ("inspect", "verify") else [])
                try:
                    with patch.object(sys, "argv", argv), redirect_stdout(output): driver.main()
                except Exception as caught:
                    error = caught
                    break
        return folder, calls, saved, error, output.getvalue()

    def test_offline_driver_protects_intent_before_exactly_one_diagnostic_dispatch(self):
        folder, calls, saved, error, output = self._offline_driver(("prepare", "start", "inspect", "verify"))
        self.assertIsNone(error)
        self.assertEqual(sum(method == "POST" for method, _, _ in calls), 1)
        self.assertEqual(saved, ["restricted-boundary-preflight", "restricted-boundary-intent", "restricted-boundary-dispatch", "restricted-boundary-observed"])
        self.assertEqual(folder.values["dispatch.json"]["intent_receipt"], folder.values["intent.json"]["protected_receipt"])
        self.assertEqual(folder.values["acceptance.json"]["status"], "observed")
        self.assertIs(folder.values["acceptance.json"]["record"]["restricted_setup_authorized"], False)

    def test_offline_driver_existing_intent_prevents_second_dispatch(self):
        _, calls, saved, error, _ = self._offline_driver(("prepare", "start", "start"))
        self.assertIsInstance(error, ValueError)
        self.assertEqual(str(error), "boundary_intent_reserved_no_redispatch")
        self.assertEqual(sum(method == "POST" for method, _, _ in calls), 1)
        self.assertEqual(saved.count("restricted-boundary-intent"), 1)

    def test_offline_driver_uncertain_dispatch_leaves_intent_and_never_retries(self):
        folder, calls, saved, error, _ = self._offline_driver(("prepare", "start"), dispatch_error=True)
        self.assertIsInstance(error, RuntimeError)
        self.assertIn("intent.json", folder.values)
        self.assertNotIn("dispatch.json", folder.values)
        self.assertEqual(sum(method == "POST" for method, _, _ in calls), 1)

    def test_offline_driver_protected_readback_failure_blocks_dispatch(self):
        folder, calls, saved, error, _ = self._offline_driver(("prepare", "start"), protected_error=True)
        self.assertIsInstance(error, ValueError)
        self.assertIn("intent.json", folder.values)
        self.assertFalse(any(method == "POST" for method, _, _ in calls))

    def test_offline_driver_source_drift_blocks_before_dispatch(self):
        _, calls, _, error, _ = self._offline_driver(("prepare", "start"), source_drift=True)
        self.assertIsInstance(error, ValueError)
        self.assertFalse(any(method == "POST" for method, _, _ in calls))


if __name__ == "__main__":
    unittest.main()
