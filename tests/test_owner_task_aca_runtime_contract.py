"""Offline inert ACA contract checks; no cloud/runtime acceptance authority."""
import ast
import base64
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import owner_task_aca_runtime_contract as contract
import qualify_owner_task_provider_runtime as previous
import owner_task_provider_runtime as runtime


PRINCIPAL = "824dd8fe-7c16-45ac-8d6b-571779872bc9"
CLIENT = "1a2715b3-f843-42a2-b4e6-f49ee5fd3c11"


def source():
    return (ROOT / contract.RUNTIME_SOURCE_PATH).read_bytes()


def descriptor():
    return dict(schema_version=1, observation="image_runtime_metadata_only", python_implementation="CPython",
        python_version="3.12.14", system="Linux", machine="x86_64", pointer_bits=64, effective_uid=10001,
        effective_gid=10001, psycopg_version="3.3.6", psycopg_binary_version="3.3.6", pq_implementation="binary",
        libpq_version=180006, libpq_build_version=180006, ca_path=runtime.CA_PATH, ca_bytes=224449,
        ca_sha256="714d457d580922dbf1d0be8bd35ba236a842b50b0072ae791582a19adef772a5",
        ca_certificates=150, pg_environment_absent=True,
        **{key: False for key in runtime.FALSE_FLAGS}, **{key: 0 for key in runtime.ZERO_COUNTS})


def observed_job():
    value = contract.definition(source(), descriptor())
    value.update(id=contract.JOB_ID, name=contract.JOB, type="Microsoft.App/jobs", location="East Asia")
    value["identity"]["userAssignedIdentities"][contract.IDENTITY_ID] = dict(principalId=PRINCIPAL, clientId=CLIENT)
    value["identity"].update(principalId=None, tenantId=None)
    value["properties"].update(provisioningState="Succeeded", workloadProfileName="Consumption", outboundIpAddresses=[])
    value["properties"]["configuration"].update(scheduleTriggerConfig=None, eventTriggerConfig=None)
    value["properties"]["template"]["containers"][0]["resources"]["ephemeralStorage"] = ""
    return value


class BundleTests(unittest.TestCase):
    def test_pinned_source_and_family_payload_exactly_match_frozen_previous_task(self):
        data = source(); result = contract.bundle(data)
        old = previous.task_spec(data, hashlib.sha256(data).hexdigest())
        self.assertEqual(contract.RUNTIME_SOURCE_SHA256, hashlib.sha256(data).hexdigest())
        self.assertEqual(contract.RUNTIME_SOURCE_SHA256, result["runtime_source_sha256"])
        self.assertEqual(contract.PAYLOAD_SHA256, old["probe_payload_sha256"])
        self.assertEqual(old["probe_payload_sha256"], result["probe_payload_sha256"])
        self.assertEqual(contract.PROBE_FAMILY_ID, previous.OPERATION_ID)
        self.assertEqual(";import time;time.sleep(120)", contract.HOLD_SUFFIX)
        self.assertEqual(120, result["hold_seconds"])
        self.assertEqual(contract.IMAGE + ' -I -B -c "' + result["args"][3].removesuffix(contract.HOLD_SUFFIX)
            + '"', old["task"]["steps"][0]["cmd"])
        self.assertEqual(["-I", "-B", "-c"], result["args"][:3])
        self.assertEqual(contract.digest(result["args"]), result["args_sha256"])
        self.assertEqual(result["args_sha256"], result["aca_args_sha256"])
        self.assertEqual(hashlib.sha256(result["args"][3].encode()).hexdigest(), result["aca_trampoline_sha256"])
        self.assertEqual(contract.digest(["-I", "-B", "-c", result["args"][3].removesuffix(contract.HOLD_SUFFIX)]),
            result["probe_args_sha256"])

    def test_payload_parser_is_exact_hash_checked_checker_without_source_execution_in_builder(self):
        result = contract.bundle(source())
        packed = re.search(r"base64\.b64decode\('([A-Za-z0-9+/=]+)'\)", result["args"][3]).group(1)
        script = gzip.decompress(base64.b64decode(packed)).decode("utf-8")
        self.assertEqual(contract.PAYLOAD_SHA256, hashlib.sha256(script.encode()).hexdigest())
        tree = ast.parse(script)
        checks = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "check_runtime"]
        self.assertEqual(1, len(checks)); self.assertEqual([], checks[0].args); self.assertEqual([], checks[0].keywords)
        self.assertIn(contract.RUNTIME_SOURCE_SHA256, script); self.assertIn(contract.PROBE_FAMILY_ID, script)
        self.assertNotIn("psycopg.connect", script); self.assertNotIn("AzureCliCredential", script)
        self.assertNotIn("secretRef", script)

    def test_unapproved_native_bytes_hash_bounds_invalid_utf8_refused(self):
        data = source()
        for bad in (None, True, "pass", bytearray(data), memoryview(data), b"", b"x" * 32769,
            b"\xff", b"pass\n", data + b"\n"):
            with self.subTest(kind=type(bad).__name__), self.assertRaises(contract.AcaRuntimeContractError) as error:
                contract.bundle(bad)
            self.assertEqual("aca_runtime_source_invalid", str(error.exception))

    def test_pure_build_never_reads_files_environment_runtime_or_connector(self):
        data = source(); expected = descriptor()
        with patch("builtins.open", side_effect=AssertionError("unexpected file")), \
            patch.object(Path, "read_bytes", side_effect=AssertionError("unexpected file")), \
            patch.object(runtime, "check_runtime", side_effect=AssertionError("unexpected runtime")), \
            patch.object(runtime.os, "getenv", side_effect=AssertionError("unexpected environment")), \
            patch.object(previous, "probe_plan", side_effect=AssertionError("unexpected old controller IO")):
            result = contract.build(data, expected)
        self.assertEqual("candidate_secretless_aca_runtime_contract", result["status"])


class NativeAndDescriptorTests(unittest.TestCase):
    def test_canonical_requires_exact_native_json_finite_depth_and_bounds(self):
        class CustomDict(dict): pass
        class CustomList(list): pass
        class CustomString(str): pass
        for bad in (CustomDict(a=1), CustomList([1]), CustomString("value"), {CustomString("key"): 1},
            {1: "value"}, float("nan"), float("inf"), b"value", (1, 2), "x" * 131073):
            with self.subTest(kind=type(bad).__name__), self.assertRaises(contract.AcaRuntimeContractError):
                contract.canonical(bad)
        cycle = []; cycle.append(cycle)
        with self.assertRaises(contract.AcaRuntimeContractError): contract.canonical(cycle)
        self.assertEqual(b'{"a":1,"b":false}', contract.canonical({"b": False, "a": 1}))

    def test_expected_descriptor_is_native_validated_and_deep_json_copied_not_provenance(self):
        expected = descriptor(); result = contract.build(source(), expected)
        self.assertEqual(expected, result["expected_runtime"])
        self.assertIsNot(expected, result["expected_runtime"])
        self.assertEqual(contract.digest(expected), result["expected_runtime_sha256"])
        expected["ca_sha256"] = "f" * 64
        self.assertNotEqual(expected, result["expected_runtime"])
        for flag in contract.FALSE_FLAGS: self.assertIs(result[flag], False)
        self.assertEqual((0, 0, 0), (result["sql_calls"], result["model_calls"], result["credential_values_read"]))

    def test_invalid_or_positive_runtime_flags_refused_without_reflecting_values(self):
        for field, value in (("effective_uid", 0), ("effective_gid", "10001"), ("python_version", "3.13.1"),
            ("psycopg_version", "3.1.9"), ("model_calls", 1), ("credential_values_read", 1),
            ("owner_runtime_active", True), ("pg_environment_absent", False), ("ca_path", "synthetic-private-input")):
            expected = descriptor(); expected[field] = value
            with self.subTest(field=field), self.assertRaises(contract.AcaRuntimeContractError) as error:
                contract.build(source(), expected)
            self.assertEqual("aca_runtime_expected_descriptor_invalid", str(error.exception))
            self.assertNotIn("synthetic-private-input", str(error.exception))
        expected = descriptor(); expected["foreign"] = "synthetic-private-input"
        with self.assertRaises(contract.AcaRuntimeContractError): contract.build(source(), expected)


class DefinitionTests(unittest.TestCase):
    def test_only_exact_manual_secretless_job_registry_identity_and_capacity(self):
        result = contract.build(source(), descriptor()); value = result["definition"]
        self.assertEqual("eastasia", value["location"])
        self.assertEqual({"type": "UserAssigned", "userAssignedIdentities": {contract.IDENTITY_ID: {}}}, value["identity"])
        props = value["properties"]; config = props["configuration"]; template = props["template"]
        self.assertEqual(contract.ENVIRONMENT, props["environmentId"])
        self.assertEqual("Manual", config["triggerType"]); self.assertEqual(600, config["replicaTimeout"])
        self.assertEqual(0, config["replicaRetryLimit"])
        self.assertEqual({"parallelism": 1, "replicaCompletionCount": 1}, config["manualTriggerConfig"])
        self.assertEqual([], config["secrets"])
        self.assertEqual([{"server": contract.REGISTRY_SERVER, "identity": contract.IDENTITY_ID}], config["registries"])
        self.assertEqual([{"identity": contract.IDENTITY_ID, "lifecycle": "None"}], config["identitySettings"])
        self.assertEqual("2025-07-01", contract.API); self.assertEqual(contract.API, result["api_version"])
        self.assertEqual([], template["volumes"]); self.assertEqual([], template["initContainers"])
        self.assertEqual(1, len(template["containers"])); container = template["containers"][0]
        self.assertEqual(contract.IMAGE, container["image"]); self.assertEqual(["python"], container["command"])
        self.assertEqual([], container["env"]); self.assertEqual({"cpu": 0.5, "memory": "1Gi"}, container["resources"])
        self.assertEqual(contract.digest(value), result["definition_sha256"])
        for forbidden in ("user", "secretRef", "username", "password", "keyVaultUrl", "volumeMounts", "probes"):
            self.assertNotIn(forbidden, container); self.assertNotIn(forbidden, config)

    def test_candidate_definition_rejects_any_schedule_secret_identity_capacity_or_argv_change(self):
        original = contract.definition(source(), descriptor())
        changes = [lambda value: value.update(location="westus"),
            lambda value: value["identity"].update(type="SystemAssigned"),
            lambda value: value["properties"].update(environmentId="foreign"),
            lambda value: value["properties"]["configuration"].update(triggerType="Schedule"),
            lambda value: value["properties"]["configuration"].update(replicaRetryLimit=True),
            lambda value: value["properties"]["configuration"].pop("identitySettings"),
            lambda value: value["properties"]["configuration"]["identitySettings"][0].update(lifecycle="All"),
            lambda value: value["properties"]["configuration"].update(secrets=[{"value": "synthetic-private"}]),
            lambda value: value["properties"]["configuration"]["registries"][0].update(passwordSecretRef="private"),
            lambda value: value["properties"]["template"]["containers"][0].update(env=[{"secretRef": "private"}]),
            lambda value: value["properties"]["template"]["containers"][0].update(user="root"),
            lambda value: value["properties"]["template"]["containers"][0]["resources"].update(cpu=1),
            lambda value: value["properties"]["template"]["containers"][0]["args"].__setitem__(3,
                value["properties"]["template"]["containers"][0]["args"][3].replace("time.sleep(120)", "time.sleep(1)")),
            lambda value: value["properties"]["template"]["containers"][0]["args"].append("foreign")]
        contract.validate_definition(original, source(), descriptor())
        for change in changes:
            value = copy.deepcopy(original); change(value)
            with self.assertRaises(contract.AcaRuntimeContractError): contract.validate_definition(value, source(), descriptor())


class TemplateTests(unittest.TestCase):
    def test_creation_and_known_server_empty_defaults_only(self):
        template = contract.definition(source(), descriptor())["properties"]["template"]
        for execution in (False, True):
            result = contract.validate_template(template, source(), descriptor(), execution=execution)
            self.assertIs(result["actual_execution_verified"], False)
            self.assertIs(result["execution_template_requested"], execution)
        observed = copy.deepcopy(template)
        observed["containers"][0].update(env=None, volumeMounts=[], probes=None)
        observed["containers"][0]["resources"]["ephemeralStorage"] = ""
        observed.update(volumes=None, initContainers=[])
        contract.validate_template(observed, source(), descriptor(), execution=True)

    def test_secrets_aliases_unknown_props_sidecars_and_resource_defaults_refused(self):
        original = contract.definition(source(), descriptor())["properties"]["template"]
        changes = [lambda value: value.update(foreign=True),
            lambda value: value.update(volumes=[{"name": "private"}]),
            lambda value: value.update(initContainers=[{"name": "private"}]),
            lambda value: value["containers"].append(copy.deepcopy(value["containers"][0])),
            lambda value: value["containers"][0].update(env=[{"name": "OWNERTRIAL_ADMIN_URL", "secretRef": "cappjob-private"}]),
            lambda value: value["containers"][0].update(env={}),
            lambda value: value["containers"][0].update(command=["sh"]),
            lambda value: value["containers"][0].update(user="10001:10001"),
            lambda value: value["containers"][0].update(probes=[{}]),
            lambda value: value["containers"][0].update(volumeMounts=[{}]),
            lambda value: value["containers"][0]["resources"].update(ephemeralStorage=None),
            lambda value: value["containers"][0]["resources"].update(ephemeralStorage="1Gi"),
            lambda value: value["containers"][0]["resources"].update(unknown=""),
            lambda value: value["containers"][0]["resources"].update(cpu=True)]
        for change in changes:
            value = copy.deepcopy(original); change(value)
            for execution in (False, True):
                with self.subTest(execution=execution), self.assertRaises(contract.AcaRuntimeContractError):
                    contract.validate_template(value, source(), descriptor(), execution=execution)
        with self.assertRaises(contract.AcaRuntimeContractError):
            contract.validate_template(original, source(), descriptor(), execution=1)


class JobAndIdentityTests(unittest.TestCase):
    def test_concrete_api_defaults_only_none_dapr_and_exact_registered_unsigned_endpoint(self):
        value = observed_job()
        value["properties"]["configuration"]["dapr"] = None
        value["properties"]["eventStreamEndpoint"] = contract.EVENT_STREAM_ENDPOINT
        self.assertEqual("https://eastasia.azurecontainerapps.dev/subscriptions/" + contract.SUBSCRIPTION
            + "/resourceGroups/" + contract.RESOURCE_GROUP + "/containerApps/" + contract.JOB + "/eventstream",
            contract.EVENT_STREAM_ENDPOINT)
        self.assertEqual("purplerock-8cff24a2.eastasia.azurecontainerapps.io", contract.ENVIRONMENT_DEFAULT_DOMAIN)
        contract.validate_job(value, source(), descriptor(), principal_id=PRINCIPAL, client_id=CLIENT)
        value["properties"].pop("eventStreamEndpoint")
        contract.validate_job(value, source(), descriptor(), principal_id=PRINCIPAL, client_id=CLIENT)
        for dapr in ({}, [], False, {"enabled": False}):
            actual = copy.deepcopy(value); actual["properties"]["configuration"]["dapr"] = dapr
            with self.subTest(dapr=dapr), self.assertRaises(contract.AcaRuntimeContractError):
                contract.validate_job(actual, source(), descriptor(), principal_id=PRINCIPAL, client_id=CLIENT)
        for endpoint in (None, True, "", contract.EVENT_STREAM_ENDPOINT + "?api-version=2025-07-01",
            contract.EVENT_STREAM_ENDPOINT + "?token=synthetic-private", contract.EVENT_STREAM_ENDPOINT + "#fragment",
            contract.EVENT_STREAM_ENDPOINT.replace("https://", "https://userinfo@"),
            contract.EVENT_STREAM_ENDPOINT.replace(contract.JOB, "ibkrv2stg-bootstrap"),
            contract.EVENT_STREAM_ENDPOINT.replace("eastasia.azurecontainerapps.dev", contract.ENVIRONMENT_DEFAULT_DOMAIN),
            contract.EVENT_STREAM_ENDPOINT.replace("https://", "http://"),
            contract.EVENT_STREAM_ENDPOINT.replace("/eventstream", "/%65ventstream")):
            actual = copy.deepcopy(value); actual["properties"]["eventStreamEndpoint"] = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(contract.AcaRuntimeContractError) as error:
                contract.validate_job(actual, source(), descriptor(), principal_id=PRINCIPAL, client_id=CLIENT)
            self.assertNotIn("synthetic-private", str(error.exception))

    def test_only_known_native_descriptive_server_metadata_without_event_stream_urls(self):
        value = observed_job()
        value["systemData"] = dict(createdBy="synthetic-principal", createdByType="Application",
            createdAt="2026-10-04T19:00:00.0000000Z", lastModifiedBy=None,
            lastModifiedByType="User", lastModifiedAt="2026-10-04T19:00:00+00:00")
        value["properties"]["outboundIpAddresses"] = ["10.0.0.1", "2001:db8::1"]
        contract.validate_job(value, source(), descriptor(), principal_id=PRINCIPAL, client_id=CLIENT)
        for change in (lambda actual: actual["systemData"].update(unknown="private"),
            lambda actual: actual["systemData"].update(createdByType="Unknown"),
            lambda actual: actual["systemData"].update(createdAt=True),
            lambda actual: actual["systemData"].update(createdAt="https://private.invalid/?token=private"),
            lambda actual: actual["properties"].update(outboundIpAddresses="10.0.0.1"),
            lambda actual: actual["properties"].update(outboundIpAddresses=["https://private.invalid"]),
            lambda actual: actual["properties"].update(outboundIpAddresses=[True]),
            lambda actual: actual["properties"].update(outboundIpAddresses=["10.0.0.1"] * 65)):
            actual = copy.deepcopy(value); change(actual)
            with self.assertRaises(contract.AcaRuntimeContractError):
                contract.validate_job(actual, source(), descriptor(), principal_id=PRINCIPAL, client_id=CLIENT)

    def test_native_job_readback_and_guid_shape_do_not_prove_authority(self):
        value = observed_job()
        result = contract.validate_job(value, source(), descriptor(), principal_id=PRINCIPAL, client_id=CLIENT)
        self.assertEqual(contract.JOB_ID, result["job_id"])
        self.assertEqual(PRINCIPAL, result["principal_id"]); self.assertEqual(CLIENT, result["client_id"])
        for flag in contract.FALSE_FLAGS: self.assertIs(result[flag], False)
        identity = contract.validate_identity(value["identity"], principal_id=PRINCIPAL, client_id=CLIENT)
        self.assertIs(identity["identity_provenance_verified"], False)
        self.assertIs(identity["effective_identity_permissions_verified"], False)

    def test_fixed_job_resource_scope_config_unknown_props_and_model_credentials_refused(self):
        original = observed_job()
        changes = [lambda value: value.update(id=contract.JOB_ID + "/foreign"),
            lambda value: value.update(name="foreign"), lambda value: value.update(type="Microsoft.App/containerApps"),
            lambda value: value.update(unknown=True), lambda value: value["tags"].update(foreign="value"),
            lambda value: value["properties"].update(environmentId="foreign"),
            lambda value: value["properties"].update(provisioningState="Updating"),
            lambda value: value["properties"].update(workloadProfileName="Dedicated"),
            lambda value: value["properties"].update(eventStreamEndpoint="https://foreign.invalid/?token=private"),
            lambda value: value["properties"]["configuration"].update(scheduleTriggerConfig={}),
            lambda value: value["properties"]["configuration"].update(eventTriggerConfig={}),
            lambda value: value["properties"]["configuration"].pop("identitySettings"),
            lambda value: value["properties"]["configuration"]["identitySettings"][0].update(lifecycle="All"),
            lambda value: value["properties"]["configuration"].update(unknown=True),
            lambda value: value["properties"]["configuration"].update(secrets=[{"name": "OPENAI_KEY", "value": "private"}]),
            lambda value: value["properties"]["configuration"]["registries"][0].update(identity="foreign")]
        for change in changes:
            value = copy.deepcopy(original); change(value)
            with self.assertRaises(contract.AcaRuntimeContractError):
                contract.validate_job(value, source(), descriptor(), principal_id=PRINCIPAL, client_id=CLIENT)

    def test_guid_native_shape_mismatch_system_identity_or_extra_mi_refused(self):
        class CustomString(str): pass
        value = observed_job()["identity"]
        for bad in (None, True, "", "A" * 36, "00000000-0000-0000-0000-000000000000", CustomString(PRINCIPAL)):
            with self.subTest(value=bad), self.assertRaises(contract.AcaRuntimeContractError):
                contract.validate_identity(value, principal_id=bad, client_id=CLIENT)
        for change in (lambda actual: actual.update(type="SystemAssigned"),
            lambda actual: actual.update(principalId=PRINCIPAL), lambda actual: actual.update(tenantId=CLIENT),
            lambda actual: actual["userAssignedIdentities"].update({"foreign": {}}),
            lambda actual: actual["userAssignedIdentities"][contract.IDENTITY_ID].update(clientId=PRINCIPAL),
            lambda actual: actual["userAssignedIdentities"][contract.IDENTITY_ID].update(extra=True)):
            actual = copy.deepcopy(value); change(actual)
            with self.assertRaises(contract.AcaRuntimeContractError):
                contract.validate_identity(actual, principal_id=PRINCIPAL, client_id=CLIENT)

    def test_native_subclass_values_and_boolean_numeric_aliases_cannot_pass(self):
        class CustomDict(dict): pass
        class CustomString(str): pass
        value = observed_job()
        for actual in (CustomDict(value), dict(value, id=CustomString(contract.JOB_ID))):
            with self.assertRaises(contract.AcaRuntimeContractError):
                contract.validate_job(actual, source(), descriptor(), principal_id=PRINCIPAL, client_id=CLIENT)
        value["properties"]["configuration"]["manualTriggerConfig"]["parallelism"] = True
        with self.assertRaises(contract.AcaRuntimeContractError):
            contract.validate_job(value, source(), descriptor(), principal_id=PRINCIPAL, client_id=CLIENT)


class InertModuleTests(unittest.TestCase):
    def test_no_cloud_sdk_controller_connector_or_module_time_io(self):
        tree = ast.parse(Path(contract.__file__).read_text(encoding="utf-8"))
        imports = []
        for node in tree.body:
            if isinstance(node, ast.Import): imports.extend(item.name for item in node.names)
            elif isinstance(node, ast.ImportFrom): imports.append(node.module)
            elif isinstance(node, ast.Expr): self.assertIsInstance(node.value, ast.Constant)
            elif isinstance(node, ast.If): self.fail("No entrypoint or module-time control flow is allowed")
        for prohibited in ("requests", "azure", "subprocess", "os", "pathlib", "qualify_owner_task_provider_runtime",
            "qualify_owner_task_standard_pull", "psycopg"):
            self.assertFalse(any(name == prohibited or name.startswith(prohibited + ".") for name in imports))
        self.assertFalse(hasattr(contract, "dispatch")); self.assertFalse(hasattr(contract, "save"))


class RuntimeRecordTests(unittest.TestCase):
    def record(self):
        return dict(operation_id=contract.PROBE_FAMILY_ID, image=contract.IMAGE,
            runtime_source_sha256=contract.RUNTIME_SOURCE_SHA256, runtime=descriptor())

    def test_reported_descriptor_full_equality_is_not_execution_or_activation_proof(self):
        result = contract.validate_runtime_record(self.record(), source(), descriptor())
        self.assertIs(result["reported_runtime_matches_expected"], True)
        for key in contract.FALSE_FLAGS: self.assertIs(result[key], False)

    def test_binding_extra_fields_and_even_valid_but_different_runtime_refused(self):
        for change in (lambda value: value.update(operation_id=contract.OPERATION_ID),
            lambda value: value.update(image="foreign"), lambda value: value.update(runtime_source_sha256="f" * 64),
            lambda value: value.update(extra=True), lambda value: value["runtime"].update(python_version="3.12.13"),
            lambda value: value["runtime"].update(ca_sha256="f" * 64),
            lambda value: value["runtime"].update(libpq_version=180005),
            lambda value: value["runtime"].update(owner_runtime_active=True)):
            value = self.record(); change(value)
            with self.assertRaises(contract.AcaRuntimeContractError):
                contract.validate_runtime_record(value, source(), descriptor())


if __name__ == "__main__":
    unittest.main()
