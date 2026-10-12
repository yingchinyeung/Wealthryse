"""Offline additive launch contracts, NOT a qualified image or live dispatch."""
import ast
import base64
import contextlib
import copy
import gzip
import hashlib
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))
import owner_task_provider_launch_contract as launch
import test_owner_task_provider_runtime as runtime_fixtures
import test_owner_task_isolated_provider_observation as worker_fixtures


def inputs():
    sources = {name: (ROOT / name).read_bytes() for name in launch.SOURCE_PATHS}
    hashes = {name: hashlib.sha256(value).hexdigest() for name, value in sources.items()}
    return sources, hashes, runtime_fixtures.descriptor()


def actual_job(value, principal="11111111-1111-1111-1111-111111111111",
               client="22222222-2222-2222-2222-222222222222"):
    job = copy.deepcopy(value)
    job.update(id=launch.registry.JOB_ID, name=launch.registry.JOB, type="Microsoft.App/jobs")
    job["identity"]["userAssignedIdentities"][launch.registry.IDENTITY_ID] = dict(principalId=principal, clientId=client)
    job["properties"]["provisioningState"] = "Succeeded"
    job["properties"]["template"]["containers"][0]["resources"]["ephemeralStorage"] = ""
    return job


def inline_source(plan):
    expression = plan["definition"]["properties"]["template"]["containers"][0]["args"][-1]
    literal = expression.split("base64.b64decode('", 1)[1].split("'", 1)[0]
    return gzip.decompress(base64.b64decode(literal)).decode("utf-8")


class NativeSubclass(dict):
    pass


class SecretEnvironment(dict):
    def __init__(self, values):
        super().__init__(values)
        self.lookups = []

    def pop(self, key, default=None):
        self.lookups.append(key)
        return super().pop(key, default)


class LaunchTests(unittest.TestCase):
    def plan(self):
        return launch.build(*inputs())

    def test_deterministic_exact_candidate_no_live_authority(self):
        plan = self.plan()
        self.assertEqual(plan, self.plan())
        self.assertEqual("candidate_catalog_only_launch_contract", plan["status"])
        self.assertEqual(launch.OPERATION_ID, plan["binding"]["operation_id"])
        self.assertEqual("provider", plan["binding"]["purpose"])
        self.assertEqual(launch.registry.JOB_ID, plan["binding"]["job_resource_id"])
        sources, hashes, expected = inputs()
        self.assertEqual(launch.digest({name: hashes[name] for name in launch.WORKER_SOURCE_PATHS}),
                         plan["worker_source_sha256"])
        self.assertEqual(hashes[launch.RUNTIME_SOURCE_PATH], plan["runtime_source_sha256"])
        self.assertEqual(launch.LAUNCHER_SOURCE_SHA256, plan["launcher_source_sha256"])
        self.assertEqual(hashlib.sha256(inline_source(plan).encode()).hexdigest(), plan["full_source_sha256"])
        self.assertEqual(launch.digest(expected), plan["expected_runtime_sha256"])
        self.assertEqual(launch.digest(plan["definition"]), plan["definition_sha256"])
        for key in ("image_runtime_qualified", "effective_identity_permissions_verified", "dispatch_authorized",
                    "provider_semantics_verified", "fresh_restricted_login_verified",
                    "model_credential_containment_verified", "owner_runtime_active",
                    "six_specialist_runtimes_active", "ibkr_source_writes"):
            self.assertIs(plan[key], False)
        self.assertNotIn(worker_fixtures.PASSWORD, launch.canonical(plan).decode())

    def test_exact_manual_identity_secret_registry_image_capacity(self):
        value = self.plan()["definition"]
        self.assertEqual(dict(type="UserAssigned", userAssignedIdentities={launch.registry.IDENTITY_ID: {}}), value["identity"])
        properties = value["properties"]
        self.assertEqual(launch.registry.ENVIRONMENT, properties["environmentId"])
        self.assertEqual(dict(triggerType="Manual", replicaTimeout=600, replicaRetryLimit=0,
            manualTriggerConfig=dict(parallelism=1, replicaCompletionCount=1),
            secrets=[dict(name=launch.registry.SECRET_NAME, keyVaultUrl=launch.SECRET_VERSION_URL,
                          identity=launch.registry.IDENTITY_ID)],
            registries=[dict(server=launch.REGISTRY_SERVER, identity=launch.registry.IDENTITY_ID)]), properties["configuration"])
        container = properties["template"]["containers"][0]
        self.assertEqual(launch.IMAGE, container["image"])
        self.assertEqual(["python"], container["command"])
        self.assertEqual(["-I", "-B", "-c"], container["args"][:3])
        self.assertNotIn("-S", container["args"])
        self.assertEqual([dict(name="OWNERTRIAL_ADMIN_URL", secretRef=launch.registry.SECRET_NAME)], container["env"])
        self.assertEqual(dict(cpu=0.5, memory="1Gi"), container["resources"])
        self.assertEqual({"containers"}, set(properties["template"]))

    def test_build_returns_fresh_native_copies(self):
        sources, hashes, descriptor = inputs()
        plan = launch.build(sources, hashes, descriptor)
        descriptor["ca_sha256"] = "b" * 64
        hashes[launch.RUNTIME_SOURCE_PATH] = "c" * 64
        self.assertEqual("a" * 64, plan["expected_runtime"]["ca_sha256"])
        self.assertNotEqual(hashes[launch.RUNTIME_SOURCE_PATH], plan["source_hashes"][launch.RUNTIME_SOURCE_PATH])
        plan["definition"]["tags"]["purpose"] = "changed"
        self.assertEqual("owner-isolated-provider-catalog", self.plan()["definition"]["tags"]["purpose"])

    def test_source_keys_native_values_hashes_and_frozen_workers(self):
        cases = []
        sources, hashes, expected = inputs()
        cases.extend([(NativeSubclass(sources), hashes), (sources, NativeSubclass(hashes)), ({}, hashes),
                      (sources, {}), ({**sources, "unregistered.py": b"pass"}, hashes)])
        for path in launch.SOURCE_PATHS:
            bad = dict(sources); bad[path] = bytearray(sources[path]); cases.append((bad, hashes))
            bad = dict(hashes); bad[path] = "A" * 64; cases.append((sources, bad))
            bad = dict(hashes); bad[path] = True; cases.append((sources, bad))
            bad = dict(sources); bad[path] += b"\n# edited\n"; cases.append((bad, hashes))
        for path in launch.WORKER_SOURCE_PATHS:
            bad = dict(sources); bad[path] += b"\n# edited\n"
            changed = dict(hashes); changed[path] = hashlib.sha256(bad[path]).hexdigest()
            cases.append((bad, changed))
        for bad_sources, bad_hashes in cases:
            with self.subTest(keys=list(bad_sources)), self.assertRaises(launch.LaunchContractError) as error:
                launch.build(bad_sources, bad_hashes, expected)
            self.assertEqual("launch_source_invalid", str(error.exception))

    def test_runtime_source_syntax_utf8_null_and_boundedness(self):
        sources, hashes, expected = inputs()
        for value in (b"", b"\xff", b"\x00", b"def :", b"x" * (launch.MAX_SOURCE_BYTES + 1)):
            changed = dict(sources); changed[launch.RUNTIME_SOURCE_PATH] = value
            wanted = dict(hashes); wanted[launch.RUNTIME_SOURCE_PATH] = hashlib.sha256(value).hexdigest()
            with self.assertRaises(launch.LaunchContractError):
                launch.build(changed, wanted, expected)

    def test_missing_or_malformed_expected_runtime_cannot_be_waived(self):
        sources, hashes, descriptor = inputs()
        for value in (None, {}, NativeSubclass(descriptor), {**descriptor, "extra": "unapproved"},
                      {**descriptor, "tls_connection_verified": True},
                      {**descriptor, "ca_sha256": "A" * 64}, {**descriptor, "sql_calls": True}):
            with self.subTest(value_type=type(value).__name__), self.assertRaises(launch.LaunchContractError):
                launch.build(sources, hashes, value)

    def test_key_only_environment_guard_does_not_copy_private_values(self):
        private = object()
        proof = launch.require_clean_environment({"OWNERTRIAL_ADMIN_URL": private, "PATH": private})
        self.assertIs(proof["environment_values_read"], False)
        self.assertIs(proof["credential_erasure_verified"], False)
        for key in ("PGHOST", "PGHOSTADDR", "PGSERVICE", "PGSERVICEFILE", "PGOPTIONS", "PGPASSWORD",
                    "PGSSLCERT", "PGSSLKEY", "PGSSLROOTCERT", "PGSYSCONFDIR", "PGFUTUREOPTION", "pgHost",
                    "SSL_CERT_FILE", "SSL_CERT_DIR", "OPENSSL_CONF", "OPENSSL_MODULES", "openssl_conf"):
            with self.subTest(key=key), self.assertRaises(launch.LaunchContractError) as error:
                launch.require_clean_environment({key: private})
            self.assertNotIn(str(private), str(error.exception))
        for environment in (None, [], NativeSubclass(), {1: private}, {"": private}, {"x\n": private},
                            {"x" * 513: private}, {"\u00e9": private}):
            with self.assertRaises(launch.LaunchContractError):
                launch.require_clean_environment(environment)

    def test_exact_definition_rejects_every_unreviewed_execution_extension(self):
        sources, hashes, descriptor = inputs()
        good = launch.definition(sources, hashes, descriptor)
        self.assertIs(launch.validate_definition(good, sources, hashes, descriptor)["dispatch_authorized"], False)
        mutations = [lambda value: value.update(extra="unreviewed"),
            lambda value: value["identity"].update(type="SystemAssigned"),
            lambda value: value["properties"]["configuration"].update(replicaRetryLimit=True),
            lambda value: value["properties"]["configuration"].update(triggerType="Schedule"),
            lambda value: value["properties"]["configuration"].update(replicaTimeout=601),
            lambda value: value["properties"]["configuration"]["manualTriggerConfig"].update(parallelism=2),
            lambda value: value["properties"]["configuration"]["secrets"][0].update(keyVaultUrl=launch.SECRET_VERSION_URL.rsplit("/", 1)[0]),
            lambda value: value["properties"]["configuration"]["secrets"][0].update(value="private-value"),
            lambda value: value["properties"]["configuration"]["registries"][0].update(username="unreviewed"),
            lambda value: value["properties"]["template"].update(volumes=[]),
            lambda value: value["properties"]["template"].update(initContainers=[]),
            lambda value: value["properties"]["template"]["containers"][0].update(image="python:latest"),
            lambda value: value["properties"]["template"]["containers"][0].update(command=["sh"]),
            lambda value: value["properties"]["template"]["containers"][0]["args"].append("unreviewed"),
            lambda value: value["properties"]["template"]["containers"][0]["env"].append(dict(name="PGHOST", value="other")),
            lambda value: value["properties"]["template"]["containers"][0]["resources"].update(cpu=True)]
        for change in mutations:
            bad = copy.deepcopy(good); change(bad)
            with self.assertRaises(launch.LaunchContractError):
                launch.validate_definition(bad, sources, hashes, descriptor)

    def test_actual_job_readback_is_metadata_not_permission_or_runtime_qualification(self):
        source, hashes, expected = inputs()
        result = launch.validate_job(actual_job(self.plan()["definition"]), source, hashes, expected,
            principal_id="11111111-1111-1111-1111-111111111111",
            client_id="22222222-2222-2222-2222-222222222222")
        self.assertIs(result["version_pinned_secret_configuration_verified"], True)
        for name in ("effective_identity_permissions_verified", "actual_execution_verified", "provider_semantics_verified",
                     "restricted_login_verified", "model_credential_containment_verified", "credentials_retained",
                     "owner_runtime_active", "six_specialist_runtimes_active", "ibkr_source_writes"):
            self.assertIs(result[name], False)

    def test_job_identity_and_configuration_mutations_rejected(self):
        source, hashes, expected = inputs()
        good = actual_job(self.plan()["definition"])
        mutations = [lambda value: value.update(id=launch.codec.CANARY_JOB_ID),
            lambda value: value.update(type="Microsoft.App/containerApps"),
            lambda value: value.update(extra="unreviewed"),
            lambda value: value["tags"].update(operation="b86898e4-09db-4e79-92e3-114b2ebc209e"),
            lambda value: value["identity"].update(type="SystemAssigned, UserAssigned"),
            lambda value: value["identity"].update(principalId="11111111-1111-1111-1111-111111111111"),
            lambda value: value["identity"]["userAssignedIdentities"].update(other={}),
            lambda value: value["identity"]["userAssignedIdentities"][launch.registry.IDENTITY_ID].update(clientId="33333333-3333-3333-3333-333333333333"),
            lambda value: value["properties"].update(provisioningState="Updating"),
            lambda value: value["properties"].update(workloadProfileName="unreviewed"),
            lambda value: value["properties"]["configuration"].update(eventTriggerConfig={}),
            lambda value: value["properties"]["configuration"].update(scheduleTriggerConfig={}),
            lambda value: value["properties"]["configuration"].update(unknown={} ),
            lambda value: value["properties"]["configuration"]["secrets"][0].update(value="private-value"),
            lambda value: value["properties"]["template"]["containers"][0].update(probes=[{}]),
            lambda value: value["properties"]["template"].update(initContainers=[{}]),
            lambda value: value["properties"]["template"]["containers"][0]["resources"].update(ephemeralStorage="2Gi")]
        for change in mutations:
            bad = copy.deepcopy(good); change(bad)
            with self.assertRaises(launch.LaunchContractError):
                launch.validate_job(bad, source, hashes, expected,
                    principal_id="11111111-1111-1111-1111-111111111111",
                    client_id="22222222-2222-2222-2222-222222222222")

    def test_identity_native_guid_validation(self):
        source, hashes, expected = inputs()
        for bad in (True, None, "", "A" * 36, "00000000-0000-0000-0000-000000000000"):
            with self.assertRaises(launch.LaunchContractError):
                launch.validate_job(actual_job(self.plan()["definition"]), source, hashes, expected,
                    principal_id=bad, client_id="22222222-2222-2222-2222-222222222222")

    def test_execution_internal_alias_is_not_a_job_key_vault_binding_proof(self):
        source, hashes, expected = inputs()
        template = self.plan()["definition"]["properties"]["template"]
        template["containers"][0]["env"][0]["secretRef"] = "cappjob-" + launch.registry.JOB
        with self.assertRaises(launch.LaunchContractError):
            launch.validate_template(template, source, hashes, expected)
        result = launch.validate_template(template, source, hashes, expected, execution=True)
        self.assertIs(result["key_vault_binding_verified"], False)
        template["containers"][0]["env"][0]["secretRef"] = "cappjob-other"
        with self.assertRaises(launch.LaunchContractError):
            launch.validate_template(template, source, hashes, expected, execution=True)
        with self.assertRaises(launch.LaunchContractError):
            launch.validate_template(template, source, hashes, expected, execution=1)

    def test_native_json_aliases_and_nonfinite_rejected(self):
        for value in (NativeSubclass(), {"x": (1,)}, {"x": float("nan")}, {"x": float("inf")}, {1: "no"},
                      {"x": b"private"}, {"x": object()}):
            with self.assertRaises(launch.LaunchContractError):
                launch.canonical(value)

    def run_inline(self, environment, *, runtime_match=True):
        sources, hashes, expected = inputs()
        observed = dict(expected)
        if not runtime_match:
            observed["ca_sha256"] = "b" * 64
        sources[launch.RUNTIME_SOURCE_PATH] = ("def validate_descriptor(value):\n    return dict(value)\n"
            + "def check_runtime():\n    return " + repr(observed) + "\n").encode()
        hashes[launch.RUNTIME_SOURCE_PATH] = hashlib.sha256(sources[launch.RUNTIME_SOURCE_PATH]).hexdigest()
        plan = launch.build(sources, hashes, expected)
        output = io.StringIO()
        raw = worker_fixtures.Raw()
        connect = Mock(return_value=raw)
        with patch.dict(sys.modules, {"os": SimpleNamespace(environ=environment)}), \
                patch("psycopg.connect", connect), patch("time.sleep") as held, \
                patch("pathlib.Path.is_file", return_value=True), \
                patch("pathlib.Path.is_symlink", return_value=False), contextlib.redirect_stdout(output):
            try:
                exec(compile(inline_source(plan), "offline-launcher", "exec"), {})
            except SystemExit as error:
                self.assertEqual(1, error.code)
        return plan, output.getvalue(), raw, connect, held

    def test_generated_trampoline_rejects_overrides_before_secret_lookup(self):
        for key in ("PGHOSTADDR", "PGSERVICEFILE", "PGOPTIONS", "pgFuture", "SSL_CERT_FILE", "OPENSSL_CONF"):
            env = SecretEnvironment({launch.SECRET_ENV: worker_fixtures.secret(), key: "private"})
            _, output, raw, connected, held = self.run_inline(env)
            self.assertEqual([], env.lookups)
            connected.assert_not_called(); held.assert_not_called()
            self.assertEqual([], raw.calls)
            self.assertNotIn(worker_fixtures.PASSWORD, output)
            self.assertIn("provider_launch_not_accepted", output)

    def test_generated_runtime_mismatch_blocks_secret_lookup_and_connector(self):
        env = SecretEnvironment({launch.SECRET_ENV: worker_fixtures.secret()})
        _, output, raw, connected, held = self.run_inline(env, runtime_match=False)
        self.assertEqual([], env.lookups)
        connected.assert_not_called(); held.assert_not_called()
        self.assertEqual([], raw.calls)
        self.assertNotIn(worker_fixtures.PASSWORD, output)

    def test_generated_missing_secret_is_sanitized_and_not_retried(self):
        env = SecretEnvironment({})
        _, output, _, connected, held = self.run_inline(env)
        self.assertEqual([launch.SECRET_ENV], env.lookups)
        connected.assert_not_called(); held.assert_not_called()
        self.assertIn("provider_launch_not_accepted", output)
        self.assertNotIn(worker_fixtures.PASSWORD, output)

    def test_generated_success_observes_only_with_fixed_worker_and_reduced_reference(self):
        env = SecretEnvironment({launch.SECRET_ENV: worker_fixtures.secret()})
        plan, output, raw, connected, held = self.run_inline(env)
        self.assertEqual([launch.SECRET_ENV], env.lookups)
        self.assertNotIn(launch.SECRET_ENV, env)
        connected.assert_called_once(); held.assert_called_once_with(300)
        self.assertTrue(raw.closed)
        lines = output.splitlines()
        record = launch.codec.decode_record(lines, launch.codec.Binding(**plan["binding"]))
        self.assertEqual(raw.catalog, record["catalog"])
        self.assertTrue(all(text.startswith("SELECT ") for text in raw.calls))
        self.assertNotIn(worker_fixtures.PASSWORD, output)
        self.assertIs(record["model_credential_isolation_verified"], False)

    def test_module_is_inert_and_static_trampoline_has_no_retry_install_or_model(self):
        source = Path(launch.__file__).read_text(encoding="utf-8")
        tree = ast.parse(source)
        self.assertFalse(any(isinstance(node, ast.If) for node in tree.body))
        self.assertNotRegex(source.split("LAUNCHER_SOURCE =", 1)[0], r"os\.environ|read_bytes\(|read_text\(|AzureCliCredential|requests\.|subprocess\.")
        self.assertNotRegex(launch.LAUNCHER_SOURCE, r"pip|subprocess|requests|Azure|retry\(|sleep\(")
        self.assertLess(launch.LAUNCHER_SOURCE.index("check_runtime()"), launch.LAUNCHER_SOURCE.index("os.environ.pop"))
        self.assertLess(launch.LAUNCHER_SOURCE.index("os.environ.pop"), launch.LAUNCHER_SOURCE.index("psycopg.connect"))
        self.assertEqual(launch.LAUNCHER_SOURCE_SHA256, hashlib.sha256(launch.LAUNCHER_SOURCE.encode()).hexdigest())


if __name__ == "__main__":
    unittest.main()
