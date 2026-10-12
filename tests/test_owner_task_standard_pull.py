"""Offline reviewed ACR pull correction; no runtime or cloud qualification."""
import ast
import base64
import copy
import hashlib
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import qualify_owner_task_standard_pull as controller
import owner_task_provider_runtime as runtime


def receipt(name):
    return dict(blob="synthetic/" + name, sha256="b" * 64,
        version_id="2026-10-04T19:00:00.0000000Z", protected_readback_verified=True)


def descriptor():
    return dict(schema_version=1, observation="image_runtime_metadata_only", python_implementation="CPython",
        python_version="3.12.11", system="Linux", machine="x86_64", pointer_bits=64, effective_uid=10001,
        effective_gid=10001, psycopg_version="3.3.6", psycopg_binary_version="3.3.6", pq_implementation="binary",
        libpq_version=180004, libpq_build_version=180004, ca_path=runtime.CA_PATH, ca_bytes=1234,
        ca_sha256="a" * 64, ca_certificates=145, pg_environment_absent=True,
        **{key: False for key in runtime.FALSE_FLAGS}, **{key: 0 for key in runtime.ZERO_COUNTS})


def probe_record(selected=None):
    selected = selected or controller.probe_plan()
    return dict(operation_id=controller.q.OPERATION_ID, image=controller.q.IMAGE,
        runtime_source_sha256=selected["runtime_source_sha256"], runtime=descriptor())


def logs(value=None, selected=None):
    return ("Engine metadata\nDigest: " + controller.q.IMAGE.split("@", 1)[1] + "\n"
        + controller.q.PREFIX + json.dumps(value or probe_record(selected), sort_keys=True, separators=(",", ":"))
        + "\nTask completed\n").encode()


def http_ack(name="ab123"):
    return dict(id=controller.q.canary.TARGET.registry_resource_id + "/runs/" + name,
        name=name, properties={"runId": name})


def local_checks():
    return dict(test_count=1963, passed_test_count=1963, skipped_tests=[], unittest_exit_code=0,
        compilation_exit_code=0, repository_policy="PASS: offline fixture", live_sql_tests_passed=False,
        financial_or_visual_acceptance=False, os_or_dependency_security_certification=False)


def manifests():
    previous = {"frozen/" + str(index) + ".py": hashlib.sha256(str(index).encode()).hexdigest()
        for index in range(717)}
    return previous, dict(previous, **{name: "a" * 64 for name in controller.ADDITIONS})


def prepared_value():
    previous, current = manifests()
    resources = dict(baseline="synthetic-34-jobs", registry="synthetic-registry")
    return dict(status="prepared_standard_pull_runtime_observation", operation_id=controller.OPERATION_ID,
        plan=controller.probe_plan(), guards=dict(resources, sources=current),
        historical=dict(frozen_sources=previous, frozen_resources=resources), local_checks=local_checks(),
        **{key: False for key in controller.FALSE_FLAGS})


def collect_fixture():
    prepared = prepared_value(); prepared_raw = dict(prepared, protected_receipt=receipt("preflight"))
    intent = dict(status="reserved_single_secretless_quickrun", operation_id=controller.OPERATION_ID,
        preflight_receipt=prepared_raw["protected_receipt"], plan=prepared["plan"],
        registry_id=controller.q.canary.TARGET.registry_resource_id, reservation_receipt=receipt("reservation"))
    intent_raw = dict(intent, protected_receipt=receipt("intent"))
    ack = dict(status="secretless_quickrun_acknowledged", operation_id=controller.OPERATION_ID,
        run_id="ab123", intent_receipt=intent_raw["protected_receipt"], raw_ack=http_ack())
    values = {"preflight.json": (prepared_raw, prepared), "dispatch-intent.json": (intent_raw, intent),
        "dispatch-ack.json": (dict(ack, protected_receipt=receipt("ack")), ack)}
    return values, prepared, intent, ack


def terminal(name="ab123", status="Succeeded"):
    return dict(id=controller.q.canary.TARGET.registry_resource_id + "/runs/" + name,
        name=name, runId=name, runType="QuickRun", status=status,
        platform={"os": "Linux", "architecture": "amd64"})


def history_fixture():
    old, current = manifests(); previous_plan = controller.q.probe_plan()
    prepared = dict(status="prepared_secretless_runtime_observation", operation_id=controller.q.OPERATION_ID,
        plan=previous_plan, guards=dict(sources=old, baseline="synthetic-34-jobs", registry="synthetic-registry"),
        local_checks=local_checks(), **{key: False for key in controller.FALSE_FLAGS})
    raw_prepared = dict(prepared, protected_receipt=receipt("old-preflight"))
    intent = dict(status="reserved_single_secretless_quickrun", operation_id=controller.q.OPERATION_ID,
        preflight_receipt=raw_prepared["protected_receipt"], plan=previous_plan,
        registry_id=controller.q.canary.TARGET.registry_resource_id, reservation_receipt=receipt("old-reservation"))
    raw_intent = dict(intent, protected_receipt=receipt("old-intent"))
    ack = dict(status="secretless_quickrun_acknowledged", operation_id=controller.q.OPERATION_ID,
        run_id="ck83", intent_receipt=raw_intent["protected_receipt"], raw_ack=http_ack("ck83"))
    raw_ack = dict(ack, protected_receipt=receipt("old-ack"))
    failed = terminal("ck83", "Failed")
    failed_logs = b"Step specified pull. Performing an explicit pull\nunauthorized: authentication required\n"
    diagnostic = dict(status="observed_failed_image_pull_runtime_not_qualified", operation_id=controller.q.OPERATION_ID,
        run_id="ck83", preflight_receipt=raw_prepared["protected_receipt"], intent_receipt=raw_intent["protected_receipt"],
        ack_receipt=raw_ack["protected_receipt"], reservation_receipt=intent["reservation_receipt"],
        attempt_consumed=True, automatic_retry=False, acceptance_recorded=False,
        raw_run_before=failed, raw_run_after=failed, full_logs=failed_logs.decode(), full_logs_bytes=len(failed_logs),
        full_logs_sha256=hashlib.sha256(failed_logs).hexdigest(), **{key: False for key in controller.FALSE_FLAGS})
    values = {"preflight.json": raw_prepared, "dispatch-intent.json": raw_intent, "dispatch-ack.json": raw_ack,
        "failure-diagnostic.json": dict(diagnostic, protected_receipt=receipt("old-diagnostic"))}
    return values, current, failed, failed_logs


class SourceAndTaskTests(unittest.TestCase):
    def manifests(self):
        old = {"frozen/" + str(index) + ".py": hashlib.sha256(str(index).encode()).hexdigest()
            for index in range(717)}
        return old, dict(old, **{name: "a" * 64 for name in controller.ADDITIONS})

    def test_exact_two_additions_preserve_every_717_hash(self):
        self.assertEqual(frozenset(("scripts/qualify_owner_task_standard_pull.py",
            "tests/test_owner_task_standard_pull.py")), controller.ADDITIONS)
        old, current = self.manifests()
        controller.validate_source_extension(old, current)
        cases = []
        changed = dict(current); changed[next(iter(old))] = "f" * 64; cases.append((old, changed))
        missing = dict(current); missing.pop(next(iter(controller.ADDITIONS))); cases.append((old, missing))
        extra = dict(current); extra["unreviewed.py"] = "a" * 64; cases.append((old, extra))
        older = dict(old); older.pop(next(iter(old))); cases.append((older, current))
        invalid = dict(current); invalid[next(iter(controller.ADDITIONS))] = "A" * 64; cases.append((old, invalid))
        for before, after in cases:
            with self.subTest(before_count=len(before), after_count=len(after)), self.assertRaises(ValueError):
                controller.validate_source_extension(before, after)

    def test_manifest_exact_native_types_no_forged_hash_values(self):
        class CustomDict(dict): pass
        class CustomString(str): pass
        old, current = self.manifests()
        for before, after in ((CustomDict(old), current), (old, CustomDict(current))):
            with self.assertRaises(ValueError): controller.validate_source_extension(before, after)
        changed = dict(current); changed[next(iter(controller.ADDITIONS))] = CustomString("a" * 64)
        with self.assertRaises(ValueError): controller.validate_source_extension(old, changed)
        for bad in (None, True, "a" * 63):
            changed = dict(current); changed[next(iter(controller.ADDITIONS))] = bad
            with self.assertRaises(ValueError): controller.validate_source_extension(old, changed)

    def test_only_reviewed_pull_false_and_user_removal_task_difference(self):
        previous = controller.q.probe_plan()
        selected = controller.probe_plan()
        expected = copy.deepcopy(previous["task"])
        expected["steps"][0]["pull"] = False
        expected["steps"][0].pop("user")
        self.assertEqual(expected, selected["task"])
        self.assertEqual(controller.q.digest(expected), selected["task_sha256"])
        for field in ("runtime_source_sha256", "probe_payload_sha256", "image"):
            self.assertEqual(previous[field], selected[field])
        self.assertEqual(previous["task"]["steps"][0]["cmd"], selected["task"]["steps"][0]["cmd"])
        self.assertIs(previous["task"]["steps"][0]["pull"], True)
        self.assertEqual("10001:10001", previous["task"]["steps"][0]["user"])
        self.assertIsNot(controller.OPERATION_ID, controller.q.OPERATION_ID)
        self.assertNotEqual(controller.OPERATION_ID, controller.q.OPERATION_ID)
        self.assertNotEqual(controller.FOLDER, controller.q.FOLDER)
        self.assertNotEqual(controller.RESERVATION, controller.q.RESERVATION)

    def test_plan_does_not_mutate_old_plan_or_controller_constants(self):
        old = controller.q.probe_plan(); snapshot = copy.deepcopy(old)
        constants = (controller.q.OPERATION_ID, controller.q.FOLDER, controller.q.RESERVATION)
        with patch.object(controller.q, "probe_plan", return_value=old):
            selected = controller.probe_plan()
        self.assertEqual(snapshot, old)
        self.assertEqual(constants, (controller.q.OPERATION_ID, controller.q.FOLDER, controller.q.RESERVATION))
        self.assertIsNot(old["task"], selected["task"])
        self.assertIsNot(old["task"]["steps"][0], selected["task"]["steps"][0])

    def test_no_secrets_env_identity_context_volumes_build_or_push(self):
        selected = controller.probe_plan(); task = selected["task"]; step = task["steps"][0]
        self.assertEqual(1, len(task["steps"])); self.assertEqual([], step["env"])
        self.assertEqual("python", step["entryPoint"])
        self.assertIs(step["disableWorkingDirectoryOverride"], True)
        self.assertIs(step["privileged"], False); self.assertIs(step["pull"], False)
        self.assertEqual((0, 0), (step["retries"], step["repeat"]))
        self.assertEqual((60, 60), (task["stepTimeout"], step["timeout"]))
        self.assertTrue(step["cmd"].startswith(controller.q.IMAGE + ' -I -B -c "'))
        for prohibited in ("user", "identity", "secrets", "volumes", "volumeMounts", "build", "push",
            "workingDirectory", "sourceLocation"):
            self.assertNotIn(prohibited, task); self.assertNotIn(prohibited, step)


class ParserTests(unittest.TestCase):
    def test_exact_family_payload_and_image_default_nonroot_identity(self):
        selected = controller.probe_plan(); record = probe_record(selected)
        self.assertEqual(record, controller.parse_logs(logs(record), selected))
        for key in ("effective_uid", "effective_gid"):
            for value in (0, 1000, "10001", True):
                changed = copy.deepcopy(record); changed["runtime"][key] = value
                with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                    controller.parse_logs(logs(changed), selected)

    def test_new_dispatch_id_cannot_replace_frozen_probe_family_id(self):
        selected = controller.probe_plan(); changed = probe_record(selected)
        changed["operation_id"] = controller.OPERATION_ID
        with self.assertRaises(ValueError): controller.parse_logs(logs(changed), selected)

    def test_complete_parser_bounds_sensitive_foreign_and_duplicate_records(self):
        selected = controller.probe_plan(); raw = logs(selected=selected)
        for bad in (None, "text", bytearray(raw), b"", b"x" * 65537, b"\xff", raw + b"\x00",
            raw + raw, raw + b"OWNER_TASK_PROVIDER_RUNTIME_FAILURE runtime_check_failed\n",
            raw + b"postgresql://private", raw + b"Authorization: Bearer synthetic-token",
            raw.replace(b'"schema_version":1', b'"schema_version":NaN'),
            raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')):
            with self.subTest(kind=type(bad).__name__), self.assertRaises(ValueError):
                controller.parse_logs(bad, selected)
        changed = probe_record(selected); changed["image"] = "foreign"
        with self.assertRaises(ValueError): controller.parse_logs(logs(changed), selected)
        digest = controller.q.IMAGE.split("@", 1)[1].encode()
        for bad in (raw.replace(b"Digest: " + digest + b"\n", b""), raw + b"Digest: " + digest + b"\n"):
            with self.assertRaises(ValueError): controller.parse_logs(bad, selected)

    def test_engine_digest_line_exact_not_substring_suffix_or_conflicting_record(self):
        selected = controller.probe_plan(); raw = logs(selected=selected)
        digest = controller.q.IMAGE.split("@", 1)[1].encode(); exact = b"Digest: " + digest
        for replacement in (exact + b"0", b"Prefix prose " + exact, exact + b" suffix prose",
            b"Digest: sha256:" + b"f" * 64, b"[" + b"x" * 101 + b"] " + exact,
            b"Digest: SHA256:" + digest.split(b":", 1)[1]):
            with self.subTest(replacement=replacement), self.assertRaises(ValueError):
                controller.parse_logs(raw.replace(exact, replacement), selected)
        for extra in (b"Digest: sha256:" + b"f" * 64, exact, b"malformed Digest: foreign"):
            with self.subTest(extra=extra), self.assertRaises(ValueError):
                controller.parse_logs(raw + extra + b"\n", selected)
        stamped = raw.replace(exact, b"[2026-10-04 19:00:00Z] " + exact)
        self.assertEqual(probe_record(selected), controller.parse_logs(stamped, selected))


class TransportTests(unittest.TestCase):
    def credential(self):
        value = Mock(); value.get_token.return_value = SimpleNamespace(token="synthetic-only-token")
        return value

    def response(self, *, status=200, raw=None):
        value = Mock(); value.status_code = status
        value.iter_content.return_value = [controller.q.canonical(http_ack()) if raw is None else raw]
        return value

    def test_exact_schedule_body_uses_host_registry_login_only(self):
        selected = controller.probe_plan(); body = controller.schedule_body(selected["task"])
        self.assertEqual({"type", "encodedTaskContent", "platform", "timeout", "agentConfiguration",
            "credentials", "isArchiveEnabled"}, set(body))
        self.assertEqual("EncodedTaskRunRequest", body["type"])
        self.assertEqual(selected["task"], json.loads(base64.b64decode(body["encodedTaskContent"], validate=True)))
        self.assertEqual({"os": "Linux", "architecture": "amd64"}, body["platform"])
        self.assertEqual(300, body["timeout"]); self.assertEqual({"cpu": 2}, body["agentConfiguration"])
        self.assertEqual({"sourceRegistry": {"loginMode": "Default"}}, body["credentials"])
        self.assertIs(body["isArchiveEnabled"], False)

    def test_any_unreviewed_task_change_rejected_before_auth_or_post(self):
        selected = controller.probe_plan(); credential = self.credential(); session = Mock()
        changes = [lambda task: task.update(extra=True),
            lambda task: task["steps"][0].update(pull=True),
            lambda task: task["steps"][0].update(user="10001:10001"),
            lambda task: task["steps"][0].update(env=["SECRET=private"]),
            lambda task: task["steps"][0].update(cmd="foreign"),
            lambda task: task["steps"][0].update(retries=True),
            lambda task: task["steps"][0].update(privileged=True)]
        for change in changes:
            task = copy.deepcopy(selected["task"]); change(task)
            with self.assertRaises(ValueError): controller.dispatch_http(task, credential=credential, session=session)
        credential.get_token.assert_not_called(); session.post.assert_not_called()

    def test_exact_single_post_no_redirect_with_bound_ack(self):
        selected = controller.probe_plan(); credential = self.credential(); session = Mock()
        response = self.response(); session.post.return_value = response
        self.assertEqual(http_ack(), controller.dispatch_http(selected["task"], credential=credential, session=session))
        credential.get_token.assert_called_once_with("https://management.azure.com/.default")
        session.post.assert_called_once_with(controller.SCHEDULE_URL,
            headers={"Authorization": "Bearer synthetic-only-token"},
            json=controller.schedule_body(selected["task"]), allow_redirects=False, timeout=(5, 30), stream=True)
        response.close.assert_called_once(); session.get.assert_not_called()

    def test_owned_session_has_zero_retry_and_no_ambient_proxy(self):
        selected = controller.probe_plan(); session = Mock(); session.post.return_value = self.response()
        with patch.object(controller.requests, "Session", return_value=session):
            self.assertEqual(http_ack(), controller.dispatch_http(selected["task"], credential=self.credential()))
        self.assertIs(session.trust_env, False)
        self.assertEqual("https://", session.mount.call_args.args[0])
        self.assertEqual(0, session.mount.call_args.args[1].max_retries.total)
        session.close.assert_called_once(); session.post.assert_called_once()

    def test_wrong_status_redirect_pending_and_non_native_are_consumed_without_retry(self):
        selected = controller.probe_plan()
        for status in (202, 201, 301, 302, 400, 401, 403, 500, True, "200"):
            response = self.response(status=status); session = Mock(); session.post.return_value = response
            with self.subTest(status=status), self.assertRaisesRegex(ValueError, "consumed.*inspect_only"):
                controller.dispatch_http(selected["task"], credential=self.credential(), session=session)
            session.post.assert_called_once(); session.get.assert_not_called(); response.close.assert_called_once()

    def test_duplicate_foreign_invalid_and_non_native_ack_refused(self):
        selected = controller.probe_plan(); correct = http_ack(); malformed = []
        for key, value in (("id", "/foreign/runs/ab123"), ("name", "other"),
            ("properties", {"runId": True}), ("properties", {"runId": "ab123; injected"}), ("properties", [])):
            changed = dict(correct); changed[key] = value; malformed.append(controller.q.canonical(changed))
        malformed.extend((b'[]', b'null', b'{"name":"ab123","name":"other","properties":{"runId":"ab123"}}',
            b'{"properties":{"runId":"ab123"},"unexpected":NaN}', b'\xff'))
        for raw in malformed:
            session = Mock(); response = self.response(raw=raw); session.post.return_value = response
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                controller.dispatch_http(selected["task"], credential=self.credential(), session=session)
            session.post.assert_called_once(); response.close.assert_called_once()

    def test_lost_oversize_bad_chunks_empty_and_cleanup_failure_sanitized(self):
        selected = controller.probe_plan()
        for failure in ("post", "stream", "oversize", "not-bytes", "empty", "response-close", "session-close"):
            response = self.response(); session = Mock(); session.post.return_value = response
            if failure == "post": session.post.side_effect = RuntimeError("synthetic-private")
            elif failure == "stream": response.iter_content.side_effect = RuntimeError("synthetic-private")
            elif failure == "oversize": response.iter_content.return_value = [b"x" * 65537]
            elif failure == "not-bytes": response.iter_content.return_value = ["synthetic-private"]
            elif failure == "empty": response.iter_content.return_value = []
            elif failure == "response-close": response.close.side_effect = RuntimeError("synthetic-private")
            else: session.close.side_effect = RuntimeError("synthetic-private")
            with patch.object(controller.requests, "Session", return_value=session), \
                self.subTest(failure=failure), self.assertRaises(ValueError) as error:
                controller.dispatch_http(selected["task"], credential=self.credential())
            self.assertNotIn("synthetic-private", str(error.exception))
            self.assertIn("consumed", str(error.exception)); session.post.assert_called_once(); session.get.assert_not_called()


class ImportTests(unittest.TestCase):
    def test_no_import_time_network_dispatch_or_file_write(self):
        tree = ast.parse(Path(controller.__file__).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.If):
                self.assertEqual("__name__ == '__main__'", ast.unparse(node.test))
            elif isinstance(node, ast.Expr):
                self.assertIsInstance(node.value, ast.Constant)


class HistoryAndGuardsTests(unittest.TestCase):
    def test_prepare_order_full_local_checks_and_current_guards_before_save(self):
        previous, current = manifests(); events = []
        resources = dict(baseline="synthetic-34-jobs", registry="synthetic-registry")
        historical = dict(frozen_sources=previous, frozen_resources=resources)
        def sources(): events.append("sources"); return current
        def save(value, name):
            events.append("save:" + name); return dict(value, protected_receipt=receipt(name))
        with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
            patch.object(controller, "sources", side_effect=sources), \
            patch.object(controller, "history", side_effect=lambda: events.append("history") or historical), \
            patch.object(controller.q.qa, "run_local_checks", side_effect=lambda: events.append("qa") or local_checks()), \
            patch.object(controller, "guards", side_effect=lambda: events.append("guards") or dict(resources, sources=current)), \
            patch.object(controller, "save", side_effect=save), patch.object(controller, "reserve") as reserve, \
            patch.object(controller, "dispatch_http") as dispatch:
            result = controller.prepare()
        self.assertEqual(["sources", "history", "qa", "guards", "sources", "save:preflight.json"], events)
        self.assertEqual("prepared_standard_pull_runtime_observation", result["status"])
        self.assertEqual(historical, result["historical"]); self.assertEqual(local_checks(), result["local_checks"])
        for key in controller.FALSE_FLAGS: self.assertIs(result[key], False)
        reserve.assert_not_called(); dispatch.assert_not_called()

    def test_prepare_source_or_resource_drift_never_retains_preflight(self):
        previous, current = manifests(); resources = dict(baseline="synthetic-34-jobs", registry="synthetic-registry")
        historical = dict(frozen_sources=previous, frozen_resources=resources)
        for mutation in ("current-source", "current-resource", "final-source"):
            selected_guards = dict(resources, sources=dict(current)); final = dict(current)
            if mutation == "current-source": selected_guards["sources"][next(iter(previous))] = "f" * 64
            elif mutation == "current-resource": selected_guards["baseline"] = "changed"
            else: final[next(iter(previous))] = "f" * 64
            with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
                patch.object(controller, "sources", side_effect=[current, final]), \
                patch.object(controller, "history", return_value=historical), \
                patch.object(controller.q.qa, "run_local_checks", return_value=local_checks()), \
                patch.object(controller, "guards", return_value=selected_guards), patch.object(controller, "save") as save, \
                self.subTest(mutation=mutation), self.assertRaises(ValueError):
                controller.prepare()
            save.assert_not_called()

    def test_history_pins_and_fresh_failed_run_readback_are_not_rearmed(self):
        values, current, failed, failed_logs = history_fixture()
        with TemporaryDirectory() as directory, patch.object(controller.q, "FOLDER", Path(directory)), \
            patch.object(controller.q.readback, "read_local", side_effect=lambda folder, name: values[name]) as reads, \
            patch.object(controller.q.readback, "receipt_binding") as pins, \
            patch.object(controller.q.deploy, "protected", side_effect=lambda value, kind:
                {key: item for key, item in value.items() if key != "protected_receipt"}) as protected, \
            patch.object(controller, "sources", return_value=current), \
            patch.object(controller.q, "verify_reservation") as reservation, \
            patch.object(controller, "show", return_value=failed) as shown, \
            patch.object(controller, "call_cli", return_value=failed_logs) as called, \
            patch.object(controller, "dispatch_http") as dispatched, patch.object(controller, "save") as save:
            result = controller.history()
        self.assertEqual(4, reads.call_count); self.assertEqual(4, protected.call_count)
        self.assertEqual([(values[name], "provider-runtime-" + name.removesuffix(".json"), controller.PINS[name])
            for name in controller.PINS], [item.args for item in pins.call_args_list])
        reservation.assert_called_once(); self.assertEqual(2, shown.call_count)
        called.assert_called_once_with("logs", "ck83"); dispatched.assert_not_called(); save.assert_not_called()
        self.assertIs(result["previous_attempt_consumed"], True)
        self.assertIs(result["previous_runtime_qualified"], False); self.assertEqual("ck83", result["previous_run_id"])

    def test_history_rejects_wrong_chain_success_claim_or_changed_full_output(self):
        for mutation in ("ack-id", "ack-name", "ack-run", "diagnostic-approval", "activation", "logs-hash", "logs-bytes"):
            values, current, failed, failed_logs = history_fixture()
            if mutation == "ack-id": values["dispatch-ack.json"]["raw_ack"]["id"] = "/foreign/runs/ck83"
            elif mutation == "ack-name": values["dispatch-ack.json"]["raw_ack"]["name"] = "foreign"
            elif mutation == "ack-run": values["dispatch-ack.json"]["raw_ack"]["properties"]["runId"] = "foreign"
            elif mutation == "diagnostic-approval": values["failure-diagnostic.json"]["acceptance_recorded"] = True
            elif mutation == "activation": values["failure-diagnostic.json"]["owner_runtime_active"] = True
            elif mutation == "logs-hash": values["failure-diagnostic.json"]["full_logs_sha256"] = "f" * 64
            else: values["failure-diagnostic.json"]["full_logs_bytes"] += 1
            with TemporaryDirectory() as directory, patch.object(controller.q, "FOLDER", Path(directory)), \
                patch.object(controller.q.readback, "read_local", side_effect=lambda folder, name: values[name]), \
                patch.object(controller.q.readback, "receipt_binding"), \
                patch.object(controller.q.deploy, "protected", side_effect=lambda value, kind:
                    {key: item for key, item in value.items() if key != "protected_receipt"}), \
                patch.object(controller, "sources", return_value=current), patch.object(controller.q, "verify_reservation"), \
                patch.object(controller, "show", return_value=failed), patch.object(controller, "call_cli", return_value=failed_logs), \
                self.subTest(mutation=mutation), self.assertRaises(ValueError):
                controller.history()

    def test_frozen_checks_719_source_extension_and_unchanged_resources_without_old_validator(self):
        prepared = prepared_value()
        with patch.object(controller, "sources", return_value=prepared["guards"]["sources"]), \
            patch.object(controller, "guards", return_value=prepared["guards"]) as guards, \
            patch.object(controller.q, "frozen") as old_validator:
            controller.frozen(prepared)
        guards.assert_called_once(); old_validator.assert_not_called()

    def test_frozen_rejects_source_resource_and_false_flag_drift(self):
        for mutation in ("source", "current-resource", "historical-resource", "false-flag", "qa", "plan"):
            prepared = prepared_value(); current = copy.deepcopy(prepared["guards"])
            if mutation == "source": current["sources"][next(iter(prepared["historical"]["frozen_sources"]))] = "f" * 64
            elif mutation == "current-resource": current["baseline"] = "changed"
            elif mutation == "historical-resource": prepared["historical"]["frozen_resources"]["baseline"] = "changed"
            elif mutation == "false-flag": prepared["six_specialist_runtimes_active"] = True
            elif mutation == "qa": prepared["local_checks"]["live_sql_tests_passed"] = True
            else: prepared["plan"]["task"]["steps"][0]["env"] = ["PRIVATE=value"]
            with patch.object(controller, "sources", return_value=current["sources"]), \
                patch.object(controller, "guards", return_value=current), self.subTest(mutation=mutation), self.assertRaises(ValueError):
                controller.frozen(prepared)

    def test_terminal_metadata_exact_identity_platform_and_no_queued_acceptance(self):
        value = terminal()
        with patch.object(controller, "call_cli", return_value=value): self.assertEqual(value, controller.show("ab123"))
        for key, changed in (("id", "/foreign/runs/ab123"), ("name", "foreign"), ("runId", "foreign"),
            ("runType", "Build"), ("status", "Queued"), ("status", "Running"),
            ("platform", {"os": "Windows", "architecture": "amd64"}),
            ("platform", {"os": "Linux", "architecture": "arm64"})):
            bad = dict(value); bad[key] = changed
            with patch.object(controller, "call_cli", return_value=bad), self.subTest(key=key), self.assertRaises(ValueError):
                controller.show("ab123")


class DispatchAndCollectionTests(unittest.TestCase):
    def test_new_reservation_and_protected_intent_before_single_post(self):
        events = []; prepared = prepared_value(); raw = dict(prepared, protected_receipt=receipt("preflight"))
        def save(value, name):
            events.append("save:" + name); return dict(value, protected_receipt=receipt(name))
        def dispatch(task):
            events.append("post"); self.assertEqual(prepared["plan"]["task"], task); return http_ack()
        with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
            patch.object(controller, "load", return_value=(raw, prepared)), \
            patch.object(controller, "frozen", side_effect=lambda _: events.append("frozen")), \
            patch.object(controller, "reserve", side_effect=lambda _: events.append("reserve") or receipt("reservation")), \
            patch.object(controller, "save", side_effect=save), \
            patch.object(controller, "verify_reservation", side_effect=lambda *args: events.append("verify-reservation")), \
            patch.object(controller, "dispatch_http", side_effect=dispatch) as dispatched:
            result = controller.dispatch()
        self.assertEqual(["frozen", "reserve", "save:dispatch-intent.json", "frozen", "verify-reservation", "post",
            "save:dispatch-ack.json"], events)
        self.assertEqual("ab123", result["run_id"]); self.assertEqual(controller.OPERATION_ID, result["operation_id"])
        dispatched.assert_called_once()

    def test_unknown_ack_fixed_outcome_retained_consumed_and_never_repeated(self):
        prepared = prepared_value(); raw = dict(prepared, protected_receipt=receipt("preflight"))
        for failure in ("transport", "foreign-ack", "outcome-retention"):
            retained = []
            with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
                patch.object(controller, "load", return_value=(raw, prepared)), patch.object(controller, "frozen"), \
                patch.object(controller, "reserve", return_value=receipt("reservation")) as reserve, \
                patch.object(controller, "verify_reservation"), \
                patch.object(controller, "dispatch_http", side_effect=RuntimeError("synthetic-private") if failure != "foreign-ack" else None,
                    return_value={"properties": {"runId": "ab123"}, "id": "/foreign/runs/ab123", "name": "ab123"}) as dispatched:
                def save(value, name):
                    if failure == "outcome-retention" and name == "dispatch-outcome.json":
                        raise RuntimeError("synthetic-private-retention")
                    (Path(directory) / name).touch(exist_ok=False)
                    retained.append((name, copy.deepcopy(value)))
                    return dict(value, protected_receipt=receipt(name))
                with patch.object(controller, "save", side_effect=save):
                    with self.subTest(failure=failure), self.assertRaises(ValueError) as error: controller.dispatch()
                    self.assertIn("consumed_inspect_only", str(error.exception)); self.assertNotIn("synthetic-private", str(error.exception))
                    self.assertTrue((Path(directory) / "dispatch-intent.json").exists())
                    self.assertFalse((Path(directory) / "dispatch-ack.json").exists())
                    with self.assertRaisesRegex(ValueError, "consumed"): controller.dispatch()
                dispatched.assert_called_once(); reserve.assert_called_once()
            if failure != "outcome-retention":
                self.assertEqual(["dispatch-intent.json", "dispatch-outcome.json"], [name for name, _ in retained])
                outcome = retained[-1][1]
                self.assertEqual("effect_unknown_ack_unverified_inspect_only", outcome["status"])
                self.assertIs(outcome["attempt_consumed"], True); self.assertIs(outcome["automatic_retry"], False)
                self.assertIs(outcome["no_effects_verified"], False); self.assertEqual(controller.OPERATION_ID, outcome["operation_id"])
                self.assertNotIn("synthetic-private", controller.q.canonical(outcome).decode())
            else: self.assertEqual(["dispatch-intent.json"], [name for name, _ in retained])

    def test_existing_intent_ack_or_unknown_outcome_always_blocks_dispatch(self):
        for name in ("dispatch-intent.json", "dispatch-ack.json", "dispatch-outcome.json"):
            with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
                patch.object(controller, "load") as load, patch.object(controller, "dispatch_http") as dispatched:
                (Path(directory) / name).touch()
                with self.subTest(name=name), self.assertRaisesRegex(ValueError, "consumed"): controller.dispatch()
                load.assert_not_called(); dispatched.assert_not_called()

    def test_failed_reservation_intent_or_readback_never_posts(self):
        prepared = prepared_value(); raw = dict(prepared, protected_receipt=receipt("preflight"))
        for failure in ("reserve", "save", "verify"):
            with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
                patch.object(controller, "load", return_value=(raw, prepared)), patch.object(controller, "frozen"), \
                patch.object(controller, "reserve", side_effect=ValueError("uncertain") if failure == "reserve" else None,
                    return_value=receipt("reservation")), \
                patch.object(controller, "save", side_effect=ValueError("uncertain") if failure == "save" else None,
                    return_value={"protected_receipt": receipt("intent")}), \
                patch.object(controller, "verify_reservation", side_effect=ValueError("uncertain") if failure == "verify" else None), \
                patch.object(controller, "dispatch_http") as dispatched:
                with self.subTest(failure=failure), self.assertRaises(ValueError): controller.dispatch()
                dispatched.assert_not_called()

    def collect_mocks(self, stack, values, prepared, raw, shown):
        from contextlib import ExitStack
        stack.enter_context(patch.object(controller, "load", side_effect=lambda name: values[name]))
        stack.enter_context(patch.object(controller, "frozen"))
        stack.enter_context(patch.object(controller, "verify_reservation"))
        reserve = stack.enter_context(patch.object(controller, "reserve"))
        dispatch = stack.enter_context(patch.object(controller, "dispatch_http"))
        show = stack.enter_context(patch.object(controller, "show", return_value=shown))
        cli = stack.enter_context(patch.object(controller, "call_cli", return_value=raw))
        save = stack.enter_context(patch.object(controller, "save", side_effect=lambda value, name:
            dict(value, protected_receipt=receipt(name))))
        return reserve, dispatch, show, cli, save

    def test_collect_success_retains_full_output_and_no_activation_claim(self):
        from contextlib import ExitStack
        values, prepared, _, _ = collect_fixture(); raw = logs(selected=prepared["plan"]); shown = terminal()
        with TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(controller, "FOLDER", Path(directory)))
            reserve, dispatch, show, cli, save = self.collect_mocks(stack, values, prepared, raw, shown)
            result = controller.collect()
        reserve.assert_not_called(); dispatch.assert_not_called(); self.assertEqual(2, show.call_count)
        cli.assert_called_once_with("logs", "ab123"); save.assert_called_once()
        self.assertEqual("acceptance.json", save.call_args.args[1])
        self.assertEqual(raw.decode(), result["full_logs"]); self.assertEqual(len(raw), result["full_logs_bytes"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result["full_logs_sha256"])
        self.assertIs(result["default_user_runtime_verified"], True); self.assertIs(result["acceptance_recorded"], True)
        self.assertIs(result["actual_root_cause_verified"], False)
        for flag in controller.FALSE_FLAGS: self.assertIs(result[flag], False)

    def test_failed_run_or_unaccepted_success_retains_diagnostic_not_acceptance(self):
        from contextlib import ExitStack
        for status, raw in (("Failed", b"Engine task failed\n"), ("Succeeded", b"No accepted runtime record\n")):
            values, prepared, _, _ = collect_fixture()
            with TemporaryDirectory() as directory, ExitStack() as stack:
                stack.enter_context(patch.object(controller, "FOLDER", Path(directory)))
                reserve, dispatch, _, _, save = self.collect_mocks(stack, values, prepared, raw, terminal(status=status))
                result = controller.collect()
            reserve.assert_not_called(); dispatch.assert_not_called()
            self.assertEqual("terminal-diagnostic.json", save.call_args.args[1])
            self.assertEqual("terminal_standard_pull_runtime_unqualified", result["status"])
            self.assertIs(result["acceptance_recorded"], False); self.assertIs(result["candidate_runtime_metadata_verified"], False)
            self.assertIs(result["engine_image_digest_observed"], False); self.assertIs(result["attempt_consumed"], True)
            self.assertIs(result["automatic_retry"], False); self.assertEqual(raw.decode(), result["full_logs"])

    def test_cache_hit_without_engine_digest_stays_unqualified_even_with_valid_runtime_marker(self):
        from contextlib import ExitStack
        values, prepared, _, _ = collect_fixture(); digest = controller.q.IMAGE.split("@", 1)[1].encode()
        raw = logs(selected=prepared["plan"]).replace(b"Digest: " + digest + b"\n", b"Image already cached\n")
        with TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(controller, "FOLDER", Path(directory)))
            _, _, _, _, save = self.collect_mocks(stack, values, prepared, raw, terminal())
            result = controller.collect()
        self.assertEqual("terminal-diagnostic.json", save.call_args.args[1])
        self.assertIs(result["acceptance_recorded"], False); self.assertIs(result["default_user_runtime_verified"], False)
        self.assertIs(result["engine_image_digest_observed"], False)
        self.assertEqual(raw.decode(), result["full_logs"])

    def test_failed_diagnostic_verify_is_read_only_and_does_not_rearm(self):
        from contextlib import ExitStack
        values, prepared, _, _ = collect_fixture(); raw = b"Engine task failed\n"
        with TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(controller, "FOLDER", Path(directory)))
            reserve, dispatch, show, cli, save = self.collect_mocks(stack, values, prepared, raw, terminal(status="Failed"))
            result = controller.collect(); retained = {key: value for key, value in result.items() if key != "protected_receipt"}
            values["terminal-diagnostic.json"] = (result, retained); save.reset_mock()
            with patch.object(Path, "mkdir") as mkdir: verified = controller.collect(True)
            save.assert_not_called(); reserve.assert_not_called(); dispatch.assert_not_called(); mkdir.assert_not_called()
        self.assertEqual("independent_standard_pull_diagnostic_verified", verified["status"])
        self.assertIs(verified["runtime_qualified"], False)
        self.assertEqual(4, show.call_count); self.assertTrue(all(item.args == ("logs", "ab123") for item in cli.call_args_list))

    def test_verify_is_fresh_read_only_and_retained_difference_refused(self):
        from contextlib import ExitStack
        values, prepared, _, _ = collect_fixture(); raw = logs(selected=prepared["plan"])
        with TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(controller, "FOLDER", Path(directory)))
            reserve, dispatch, _, _, save = self.collect_mocks(stack, values, prepared, raw, terminal())
            result = controller.collect(); accepted = {key: value for key, value in result.items() if key != "protected_receipt"}
            values["acceptance.json"] = (result, accepted); save.reset_mock()
            with patch.object(Path, "mkdir") as mkdir: verified = controller.collect(True)
            save.assert_not_called(); reserve.assert_not_called(); dispatch.assert_not_called(); mkdir.assert_not_called()
            self.assertEqual("independent_standard_pull_runtime_verified", verified["status"])
            accepted["full_logs_sha256"] = "f" * 64
            with self.assertRaisesRegex(ValueError, "retained_evidence_differs"): controller.collect(True)
            save.assert_not_called()

    def test_dispatch_chain_identity_status_family_plan_and_run_drift_cannot_save(self):
        from contextlib import ExitStack
        for mutation in ("intent-status", "ack-id", "ack-name", "ack-run", "ack-receipt", "operation", "plan"):
            values, prepared, intent, ack = collect_fixture(); raw = logs(selected=prepared["plan"])
            if mutation == "intent-status": intent["status"] = "foreign"
            elif mutation == "ack-id": ack["raw_ack"]["id"] = "/foreign/runs/ab123"
            elif mutation == "ack-name": ack["raw_ack"]["name"] = "foreign"
            elif mutation == "ack-run": ack["raw_ack"]["properties"]["runId"] = "foreign"
            elif mutation == "ack-receipt": ack["intent_receipt"] = receipt("foreign")
            elif mutation == "operation": ack["operation_id"] = controller.q.OPERATION_ID
            else: intent["plan"] = controller.q.probe_plan()
            with TemporaryDirectory() as directory, ExitStack() as stack:
                stack.enter_context(patch.object(controller, "FOLDER", Path(directory)))
                _, _, show, cli, save = self.collect_mocks(stack, values, prepared, raw, terminal())
                with self.subTest(mutation=mutation), self.assertRaises(ValueError): controller.collect()
                show.assert_not_called(); cli.assert_not_called(); save.assert_not_called()
        values, prepared, _, _ = collect_fixture()
        with TemporaryDirectory() as directory, ExitStack() as stack:
            stack.enter_context(patch.object(controller, "FOLDER", Path(directory)))
            _, _, show, _, save = self.collect_mocks(stack, values, prepared, logs(selected=prepared["plan"]), terminal())
            show.side_effect = [terminal(), dict(terminal(), changed=True)]
            with self.assertRaisesRegex(ValueError, "drift_during_collection"): controller.collect()
            save.assert_not_called()


if __name__ == "__main__":
    unittest.main()
