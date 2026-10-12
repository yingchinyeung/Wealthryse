"""Embedded native API fixtures; no live, source-acceptance or authority proof."""
import contextlib
import copy
import importlib
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import owner_task_aca_native as native
import qualify_owner_task_aca_runtime_metadata as oldm
from test_owner_task_aca_runtime_named import (EXECUTION, REPLICA, PRINCIPAL, CLIENT, new_job,
    native_execution, plan_pf, observed, console_row)
from test_owner_task_aca_runtime_metadata import metadata_job
from test_owner_task_aca_runtime_contract import descriptor


def execution(*, terminal=False):
    value = native_execution(terminal=terminal)
    template = value["properties"]["template"]
    template.pop("volumes", None); template["initContainers"] = []
    container = template["containers"][0]
    container.pop("env", None); container["imageType"] = "ContainerImage"
    container["resources"]["ephemeralStorage"] = ""
    return value


def endpoint_values():
    prefix = ("eastasia.azurecontainerapps.dev/subscriptions/3956e821-cee4-403f-8708-e84e2ed490eb"
        "/resourceGroups/rg-ibkr-database-v11")
    tail = "/replicas/" + REPLICA + "/containers/secretless-runtime-canary"
    return dict(execEndpoint="wss://" + prefix + "/containerApps/ibkrv2stg-ownerruntime-20261004/revisions/"
        + EXECUTION + tail + "/exec", logStreamEndpoint="https://" + prefix
        + "/jobs/ibkrv2stg-ownerruntime-20261004/executions/" + EXECUTION + tail + "/logstream")


def nested_replica(*, ready=True, endpoints=True):
    container = dict(name="secretless-runtime-canary", ready=ready, started=ready, restartCount=0,
        runningState="Running" if ready else "Terminated", runningStateDetails="" if ready
            else "Container was terminated with reason: ProcessExited, exit code: 0.")
    if endpoints: container.update(endpoint_values())
    return dict(value=[dict(id=oldm.JOB_ID + "/executions/" + EXECUTION + "/replicas/" + REPLICA,
        name=REPLICA, type="Microsoft.App/containerapps/revisions/replicas", properties=dict(
            createdTime="2026-10-04T22:39:00Z", runningState="Running" if ready else "NotRunning",
            runningStateDetails="", containers=[container], initContainers=[]))], nextLink=None)


class OfflineTestCase(unittest.TestCase):
    def setUp(self):
        self.guard = contextlib.ExitStack(); self.addCleanup(self.guard.close)
        for target in ("azure.identity.AzureCliCredential.get_token", "requests.sessions.Session.request",
                "subprocess.run", "subprocess.check_output"):
            self.guard.enter_context(patch(target, side_effect=AssertionError("offline fixture attempted external access")))


class InertBoundaryTests(OfflineTestCase):
    def test_import_is_inert_and_exposes_no_effect_or_credential_adapter(self):
        with patch("os.getenv", side_effect=AssertionError("environment discovery")), \
                patch.object(oldm, "validate_execution", side_effect=AssertionError("validation ran on import")), \
                patch.object(oldm, "validate_job_metadata", side_effect=AssertionError("validation ran on import")):
            importlib.reload(native)
        self.assertEqual("ibkrv2stg-ownerruntime-20261004", native.JOB)
        self.assertEqual(oldm.JOB_ID, native.JOB_ID)
        for name in ("main", "start", "install", "arm", "reserve", "connect", "AzureCliCredential", "LogsTransport"):
            self.assertFalse(hasattr(native, name))

    def test_native_subclasses_nonfinite_or_copy_hooks_never_gain_parser_authority(self):
        class Hook(dict):
            def __deepcopy__(self, memo): raise AssertionError("deepcopy hook ran")
            def items(self): raise AssertionError("items hook ran")
        class StringHook(str):
            def __str__(self): raise AssertionError("string hook ran")
        for value in (Hook(execution()), {"properties": StringHook("value")}, float("nan"), (execution(),)):
            with self.subTest(kind=type(value).__name__), self.assertRaises(native.NativeResponseError):
                native.validate_execution(value, plan_pf(), EXECUTION, terminal=False)
        with self.assertRaises(native.NativeResponseError): native.classify_replica(Hook(nested_replica()), EXECUTION)

    def test_job_adapter_delegates_unchanged_literal_metadata_contract(self):
        value = new_job(); expected = copy.deepcopy(value)
        with patch.object(oldm, "validate_job_metadata", return_value=expected) as frozen:
            self.assertEqual(expected, native.validate_job_metadata(value, descriptor(), PRINCIPAL, CLIENT))
        frozen.assert_called_once_with(value, descriptor(), PRINCIPAL, CLIENT)
        # This embedded161-IP fixture is intentionally NOT the real fixed hash.
        with self.assertRaises(native.NativeResponseError):
            native.validate_job_metadata(metadata_job(), descriptor(), PRINCIPAL, CLIENT)
        with patch.object(oldm, "validate_job_metadata", side_effect=ValueError("sensitive provider value")), \
                self.assertRaises(native.NativeResponseError) as caught:
            native.validate_job_metadata(value, descriptor(), PRINCIPAL, CLIENT)
        self.assertNotIn("sensitive provider value", str(caught.exception))


class ExecutionTests(OfflineTestCase):
    def test_observed_native_containerimage_and_empty_defaults_preserve_raw(self):
        for terminal in (False, True):
            value = execution(terminal=terminal); before = copy.deepcopy(value)
            returned = native.validate_execution(value, plan_pf(), EXECUTION, terminal=terminal)
            self.assertEqual(before["properties"], returned); self.assertEqual(before, value)
            self.assertEqual("ContainerImage", returned["template"]["containers"][0]["imageType"])
            self.assertNotIn("env", returned["template"]["containers"][0])
            self.assertIsNot(value["properties"], returned)

    def test_explicit_zero_offset_execution_timestamps_preserve_native_bytes(self):
        for terminal in (False, True):
            value = execution(terminal=terminal)
            for key in ("startTime", "endTime"):
                if value["properties"][key] is not None:
                    value["properties"][key] = value["properties"][key].replace("Z", "+00:00")
            before = copy.deepcopy(value)
            returned = native.validate_execution(value, plan_pf(), EXECUTION, terminal=terminal)
            self.assertEqual(before["properties"], returned); self.assertEqual(before, value)
            self.assertTrue(returned["startTime"].endswith("+00:00"))

    def test_absent_imagetype_still_uses_the_original_exact_contract(self):
        value = native_execution(); before = copy.deepcopy(value)
        returned = native.validate_execution(value, plan_pf(), EXECUTION, terminal=True)
        self.assertEqual(before["properties"], returned); self.assertEqual(before, value)

    def test_only_imagetype_is_removed_in_detached_frozen_validation_shadow(self):
        value = execution(); before = copy.deepcopy(value); captured = []
        def check(shadow, pf, name, *, terminal):
            captured.append(copy.deepcopy(shadow)); shadow["properties"]["template"]["containers"][0]["image"] = "untrusted callback"
            pf["prerequisite"] = {}; return {}
        with patch.object(oldm, "validate_execution", side_effect=check):
            returned = native.validate_execution(value, plan_pf(), EXECUTION, terminal=False)
        expected = copy.deepcopy(before); del expected["properties"]["template"]["containers"][0]["imageType"]
        self.assertEqual([expected], captured); self.assertEqual(before["properties"], returned); self.assertEqual(before, value)

    def test_imagetype_other_values_types_locations_or_unknown_fields_reject(self):
        for field in (None, True, 0, "", "containerimage", "Artifact", "ContainerImage ", ["ContainerImage"]):
            value = execution(); value["properties"]["template"]["containers"][0]["imageType"] = field
            with self.subTest(value=field), self.assertRaises(native.NativeResponseError):
                native.validate_execution(value, plan_pf(), EXECUTION, terminal=False)
        for place in ("template", "properties", "resources", "extra"):
            value = execution(); container = value["properties"]["template"]["containers"][0]
            if place == "template": value["properties"]["template"]["imageType"] = "ContainerImage"
            elif place == "properties": value["properties"]["imageType"] = "ContainerImage"
            elif place == "resources": container["resources"]["imageType"] = "ContainerImage"
            else: container["anotherDefault"] = ""
            with self.subTest(place=place), self.assertRaises(native.NativeResponseError):
                native.validate_execution(value, plan_pf(), EXECUTION, terminal=False)

    def test_identity_time_template_and_capacity_drift_remain_strict(self):
        for change in ("id", "name", "type", "image", "command", "args", "cpu", "memory", "env", "sidecar", "volume", "init", "time", "status"):
            value = execution(terminal=True); container = value["properties"]["template"]["containers"][0]
            if change == "id": value["id"] += "-foreign"
            elif change == "name": value["name"] += "-foreign"
            elif change == "type": value["type"] = "Microsoft.App/containerApps/executions"
            elif change == "image": container["image"] += "-foreign"
            elif change == "command": container["command"] = ["sh"]
            elif change == "args": container["args"][-1] += "untrusted"
            elif change == "cpu": container["resources"]["cpu"] = True
            elif change == "memory": container["resources"]["memory"] = "2Gi"
            elif change == "env": container["env"] = [{"name": "SECRET", "value": "synthetic"}]
            elif change == "sidecar": value["properties"]["template"]["containers"].append(copy.deepcopy(container))
            elif change == "volume": value["properties"]["template"]["volumes"] = [{}]
            elif change == "init": value["properties"]["template"]["initContainers"] = [{}]
            elif change == "time": value["properties"]["startTime"] = "2026-10-04T20:30:00"
            else: value["properties"]["status"] = "Completed"
            with self.subTest(change=change), self.assertRaises(native.NativeResponseError):
                native.validate_execution(value, plan_pf(), EXECUTION, terminal=True)

    def test_boolean_mode_alias_and_naive_or_non_utc_endtime_never_relaxed(self):
        for flag in (None, 0, 1, "true"):
            with self.assertRaises(native.NativeResponseError): native.validate_execution(execution(), plan_pf(), EXECUTION, terminal=flag)
        for timestamp in ("2026-10-04T20:32:01", "2026-10-04T20:32:01+01:00", "2026-02-30T20:32:01Z"):
            value = execution(terminal=True); value["properties"]["endTime"] = timestamp
            with self.assertRaises(native.NativeResponseError): native.validate_execution(value, plan_pf(), EXECUTION, terminal=True)

    def test_fixed_local_source_or_invalid_runtime_descriptor_rejects_before_qualification(self):
        value = execution(); p = plan_pf()
        with patch.object(oldm.frozen, "source_bytes", return_value=b"not-the-approved-runtime-source"), self.assertRaises(native.NativeResponseError):
            native.validate_execution(value, p, EXECUTION, terminal=False)
        p = plan_pf(); p["prerequisite"]["expected_runtime"]["ca_sha256"] = True
        with self.assertRaises(native.NativeResponseError): native.validate_execution(value, p, EXECUTION, terminal=False)


class ReplicaTests(OfflineTestCase):
    def test_nested_native_running_row_is_validated_without_flattening_or_invention(self):
        value = nested_replica(); before = copy.deepcopy(value)
        result = native.validate_replica(value, EXECUTION)
        self.assertEqual(before["value"][0], result); self.assertEqual(before, value)
        self.assertEqual({"id", "name", "type", "properties"}, set(result)); self.assertNotIn("ready", result)
        self.assertEqual("ready", native.classify_replica(value, EXECUTION)); self.assertIsNot(value["value"][0], result)
        result["properties"]["containers"][0]["ready"] = False
        self.assertTrue(value["value"][0]["properties"]["containers"][0]["ready"])

    def test_exact_observed_unsigned_endpoint_paths_are_constructed_never_fetched(self):
        self.assertEqual(endpoint_values(), native.endpoint_metadata(EXECUTION, REPLICA))
        value = nested_replica(); row = native.validate_replica(value, EXECUTION)
        self.assertEqual(endpoint_values()["execEndpoint"], row["properties"]["containers"][0]["execEndpoint"])
        self.assertTrue(endpoint_values()["execEndpoint"].startswith("wss://"))

    def test_endpoints_are_optional_but_present_values_must_be_exact(self):
        value = nested_replica(endpoints=False); native.validate_replica(value, EXECUTION)
        for key in ("execEndpoint", "logStreamEndpoint"):
            for changed in (None, True, "", endpoint_values()[key] + "?token=untrusted", endpoint_values()[key] + "#fragment",
                    endpoint_values()[key].replace(EXECUTION, EXECUTION + "-foreign"),
                    endpoint_values()[key].replace(REPLICA, REPLICA + "-foreign"),
                    endpoint_values()[key].replace("secretless-runtime-canary", "other-container"),
                    endpoint_values()[key].replace("eastasia.azurecontainerapps.dev", "foreign.invalid"),
                    endpoint_values()[key].replace("resourceGroups", "resourcegroups")):
                raw = nested_replica(); raw["value"][0]["properties"]["containers"][0][key] = changed
                with self.subTest(key=key, changed_type=type(changed).__name__), self.assertRaises(native.NativeResponseError):
                    native.validate_replica(raw, EXECUTION)

    def test_terminal_observed_shape_is_preserved_but_never_pending_or_log_authority(self):
        value = nested_replica(ready=False); before = copy.deepcopy(value)
        with self.assertRaises(native.NativeResponseError): native.classify_replica(value, EXECUTION)
        self.assertEqual(before["value"][0], native.validate_replica(value, EXECUTION, running=False))
        with self.assertRaises(native.NativeResponseError): native.validate_replica(value, EXECUTION)
        with self.assertRaises(native.NativeResponseError): native.validate_log_binding([console_row()], value["value"][0], plan_pf())
        self.assertEqual(before, value)

    def test_explicit_valid_notready_states_are_pending_only_after_full_structure_check(self):
        for state in ("Running", "NotRunning", "Unknown"):
            value = nested_replica(); props = value["value"][0]["properties"]; container = props["containers"][0]
            props["runningState"] = state; container["ready"] = False
            if state != "Running": container["runningState"] = "Waiting"; container["started"] = False
            with self.subTest(state=state):
                self.assertEqual("pending", native.classify_replica(value, EXECUTION))
                with self.assertRaises(native.ReplicaPending): native.validate_replica(value, EXECUTION)
            container["restartCount"] = 1
            with self.assertRaises(native.NativeResponseError): native.classify_replica(value, EXECUTION)

    def test_complete_empty_inventory_is_pending_without_inventing_a_replica(self):
        for value in ({"value": []}, {"value": [], "nextLink": None}, {"value": [], "nextLink": ""}):
            self.assertEqual("pending", native.classify_replica(value, EXECUTION))
            for running in (False, True):
                with self.assertRaises(native.ReplicaPending): native.validate_replica(value, EXECUTION, running=running)
        for value in ({"value": [], "nextLink": False}, {"value": [], "nextLink": "more"},
                {"value": [], "unknown": 0}, {"value": None}, {"value": ()}):
            with self.assertRaises(native.NativeResponseError): native.classify_replica(value, EXECUTION)

    def test_unrecognized_or_contradictory_native_states_never_become_pending(self):
        for replica_state, state, ready, started in (("Preparing", "Waiting", False, False),
                ("Unknown", "ContainerCreating", False, False), ("Unknown", "Running", True, True),
                ("NotRunning", "Running", True, True), ("Running", "Running", True, False),
                ("Unknown", "Waiting", False, True), ("NotRunning", "Waiting", True, False),
                ("Running", "Terminated", False, False)):
            value = nested_replica(); props = value["value"][0]["properties"]; container = props["containers"][0]
            props["runningState"] = replica_state
            container.update(runningState=state, ready=ready, started=started)
            with self.subTest(states=(replica_state, state, ready, started)), self.assertRaises(native.NativeResponseError):
                native.classify_replica(value, EXECUTION)

    def test_known_nonterminal_status_lag_is_only_pending_never_ready_evidence(self):
        for replica_state in ("Running", "NotRunning", "Unknown"):
            for state, started in (("Waiting", False), ("Running", False), ("Running", True)):
                value = nested_replica(); props = value["value"][0]["properties"]; container = props["containers"][0]
                props["runningState"] = replica_state; container.update(runningState=state, ready=False, started=started)
                before = copy.deepcopy(value)
                with self.subTest(states=(replica_state, state, started)):
                    self.assertEqual("pending", native.classify_replica(value, EXECUTION))
                    with self.assertRaises(native.ReplicaPending): native.validate_replica(value, EXECUTION)
                    self.assertEqual(before["value"][0], native.validate_replica(value, EXECUTION, running=False))
                    with self.assertRaises(native.ReplicaPending): native.validate_log_binding([console_row()], before["value"][0], plan_pf())
                    self.assertEqual(before, value)

    def test_missing_invented_boolean_wrong_type_restart_and_state_are_structural_not_pending(self):
        for key, bad in (("ready", None), ("ready", 1), ("started", 0), ("restartCount", False),
                ("restartCount", 1), ("restartCount", -1), ("restartCount", 0.0), ("runningState", ""),
                ("runningState", None), ("runningState", "unsafe\nstate"), ("runningState", "x" * 65)):
            value = nested_replica(); value["value"][0]["properties"]["containers"][0][key] = bad
            with self.subTest(key=key, value=bad), self.assertRaises(native.NativeResponseError): native.classify_replica(value, EXECUTION)
        for key in ("ready", "started", "restartCount", "runningState"):
            value = nested_replica(); del value["value"][0]["properties"]["containers"][0][key]
            with self.assertRaises(native.NativeResponseError): native.classify_replica(value, EXECUTION)

    def test_unknown_fields_wrong_shape_sidecars_initcontainers_and_pagination_are_denied(self):
        for change in ("root", "row", "properties", "container", "flat", "duplicate", "sidecar", "init", "initnull", "partial", "boolnext"):
            value = nested_replica(); row = value["value"][0]; props = row["properties"]
            if change == "root": value["unknown"] = 0
            elif change == "row": row["unknown"] = 0
            elif change == "properties": props["unknown"] = 0
            elif change == "container": props["containers"][0]["imageType"] = "ContainerImage"
            elif change == "flat": row.update(props); del row["properties"]
            elif change == "duplicate": value["value"].append(copy.deepcopy(row))
            elif change == "sidecar": props["containers"].append(copy.deepcopy(props["containers"][0]))
            elif change == "init": props["initContainers"] = [{}]
            elif change == "initnull": props["initContainers"] = None
            elif change == "partial": value["nextLink"] = "https://foreign.invalid/continue"
            else: value["nextLink"] = False
            with self.subTest(change=change), self.assertRaises(native.NativeResponseError): native.classify_replica(value, EXECUTION)

    def test_wrong_identity_container_createdtime_or_descriptive_fields_are_denied(self):
        for change in ("job", "name", "type", "container", "time", "offset", "details", "detailtype", "state", "unknownname"):
            value = nested_replica(); row = value["value"][0]; props = row["properties"]
            if change == "job": row["id"] = row["id"].replace(native.JOB, oldm.contract.JOB)
            elif change == "name": row["name"] += "-foreign"
            elif change == "type": row["type"] = "Microsoft.App/jobs/replicas"
            elif change == "container": props["containers"][0]["name"] = "other-container"
            elif change == "time": props["createdTime"] = "2026-10-04T22:39:00"
            elif change == "offset": props["createdTime"] = "2026-10-04T22:39:00+01:00"
            elif change == "details": props["runningStateDetails"] = "sensitive\nvalue"
            elif change == "detailtype": props["containers"][0]["runningStateDetails"] = None
            elif change == "state": props["runningState"] = "x" * 65
            else: row["name"] = "foreign-family-abcdefg-abcde"
            with self.subTest(change=change), self.assertRaises(native.NativeResponseError): native.validate_replica(value, EXECUTION)

    def test_known_optional_empty_details_init_omission_and_utc_suffix_preserve_raw(self):
        value = nested_replica(endpoints=False); props = value["value"][0]["properties"]
        del props["initContainers"]; del props["runningStateDetails"]; del props["containers"][0]["runningStateDetails"]
        props["createdTime"] = "2026-10-04T22:39:00+00:00"
        before = copy.deepcopy(value); self.assertEqual(before["value"][0], native.validate_replica(value, EXECUTION)); self.assertEqual(before, value)
        for invalid in (None, 0, 1, "false"):
            with self.assertRaises(native.NativeResponseError): native.validate_replica(value, EXECUTION, running=invalid)


class LogBindingTests(OfflineTestCase):
    def test_ready_nested_replica_one_full_event_binds_unchanged_runtime_record(self):
        replica = nested_replica()["value"][0]; rows = [console_row()]; p = plan_pf(); before = copy.deepcopy((rows, replica, p))
        result = native.validate_log_binding(rows, replica, p)
        self.assertEqual(observed(), result); self.assertEqual(before, (rows, replica, p))

    def test_extra_foreign_logs_source_descriptor_or_container_drift_are_denied(self):
        for change in ("extra", "group", "container", "source", "runtime", "prefix", "newline"):
            rows = [console_row()]; p = plan_pf(); replica = nested_replica()["value"][0]
            if change == "extra": rows.append(copy.deepcopy(rows[0]))
            elif change == "group": rows[0]["Group"] += "-foreign"
            elif change == "container": rows[0]["Container"] = "other-container"
            elif change in ("source", "runtime"):
                record = copy.deepcopy(observed())
                if change == "source": record["runtime_source_sha256"] = "f" * 64
                else: record["runtime"]["ca_sha256"] = "f" * 64
                rows[0]["Log"] = oldm.contract.PREFIX + json.dumps(record, sort_keys=True, separators=(",", ":"))
            elif change == "prefix": rows[0]["Log"] = "unregistered " + rows[0]["Log"]
            else: rows[0]["Log"] += "\n"
            with self.subTest(change=change), self.assertRaises(native.NativeResponseError): native.validate_log_binding(rows, replica, p)

    def test_flat_or_unready_replica_cannot_lend_log_ownership(self):
        row = nested_replica()["value"][0]; flat = copy.deepcopy(row); flat.update(flat.pop("properties"))
        with self.assertRaises(native.NativeResponseError): native.validate_log_binding([console_row()], flat, plan_pf())
        row = nested_replica(ready=False)["value"][0]
        with self.assertRaises(native.NativeResponseError): native.validate_log_binding([console_row()], row, plan_pf())


if __name__ == "__main__": unittest.main()
