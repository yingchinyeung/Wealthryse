"""Offline controller refusal and ordering checks; no image qualification."""
import ast
import base64
import copy
import gzip
import hashlib
import json
from pathlib import Path
import re
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import qualify_owner_task_provider_runtime as controller
import owner_task_provider_runtime as runtime


def runtime_descriptor():
    return dict(schema_version=1, observation="image_runtime_metadata_only", python_implementation="CPython",
        python_version="3.12.11", system="Linux", machine="x86_64", pointer_bits=64, effective_uid=10001,
        effective_gid=10001, psycopg_version="3.3.6", psycopg_binary_version="3.3.6", pq_implementation="binary",
        libpq_version=180004, libpq_build_version=180004, ca_path=runtime.CA_PATH, ca_bytes=1234,
        ca_sha256="a" * 64, ca_certificates=145, pg_environment_absent=True,
        **{key: False for key in runtime.FALSE_FLAGS}, **{key: 0 for key in runtime.ZERO_COUNTS})


def plan():
    source = (ROOT / controller.RUNTIME_SOURCE).read_bytes()
    return controller.task_spec(source, hashlib.sha256(source).hexdigest())


def record(selected_plan=None):
    selected_plan = selected_plan or plan()
    return dict(operation_id=controller.OPERATION_ID, image=controller.IMAGE,
        runtime_source_sha256=selected_plan["runtime_source_sha256"], runtime=runtime_descriptor())


def logs(value=None, selected_plan=None, prefix=""):
    value = value if value is not None else record(selected_plan)
    return ("Engine metadata\nDigest: " + controller.IMAGE.split("@", 1)[1] + "\n" + prefix
        + controller.PREFIX + json.dumps(value, sort_keys=True, separators=(",", ":"))
        + "\nTask completed\n").encode("utf-8")


def receipt(name):
    return dict(blob="synthetic/" + name, sha256="b" * 64, version_id="2026-10-04T17:00:00.0000000Z",
        protected_readback_verified=True)


def http_ack(name="ab123"):
    return dict(id=controller.canary.TARGET.registry_resource_id + "/runs/" + name, name=name,
        properties={"runId": name})


def prepared_value(selected_plan=None):
    return dict(status="prepared_secretless_runtime_observation", operation_id=controller.OPERATION_ID,
        plan=selected_plan or plan(), guards={"synthetic": "unchanged"},
        **{key: False for key in controller.FALSE_FLAGS})


def collect_fixture():
    prepared = prepared_value()
    prepared_raw = dict(prepared, protected_receipt=receipt("preflight"))
    intent = dict(status="reserved_single_secretless_quickrun", operation_id=controller.OPERATION_ID,
        preflight_receipt=prepared_raw["protected_receipt"], plan=prepared["plan"],
        registry_id=controller.canary.TARGET.registry_resource_id, reservation_receipt=receipt("reservation"))
    intent_raw = dict(intent, protected_receipt=receipt("intent"))
    ack = dict(status="secretless_quickrun_acknowledged", operation_id=controller.OPERATION_ID,
        run_id="ab123", intent_receipt=intent_raw["protected_receipt"], raw_ack=http_ack())
    ack_raw = dict(ack, protected_receipt=receipt("ack"))
    values = {"preflight.json": (prepared_raw, prepared), "dispatch-intent.json": (intent_raw, intent),
        "dispatch-ack.json": (ack_raw, ack)}
    return values, prepared, intent, ack


class SourceAndTaskTests(unittest.TestCase):
    def manifests(self):
        old = {"frozen/" + str(index) + ".py": hashlib.sha256(str(index).encode()).hexdigest()
            for index in range(711)}
        return old, dict(old, **{name: "a" * 64 for name in controller.ADDITIONS})

    def test_only_six_new_sources_and_all_711_previous_hashes(self):
        old, current = self.manifests()
        self.assertEqual(6, len(controller.ADDITIONS))
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

    def test_manifest_native_types_required(self):
        class CustomDict(dict): pass
        class CustomString(str): pass
        old, current = self.manifests()
        for before, after in ((CustomDict(old), current), (old, CustomDict(current))):
            with self.assertRaises(ValueError): controller.validate_source_extension(before, after)
        for name in controller.ADDITIONS:
            changed = dict(current); changed[name] = CustomString(changed[name])
            with self.assertRaises(ValueError): controller.validate_source_extension(old, changed)

    def test_native_bound_exact_runtime_source_bytes(self):
        source = b"pass\n"; sha = hashlib.sha256(source).hexdigest()
        controller.task_spec(source, sha)
        for bad in (None, "pass", bytearray(source), memoryview(source), b"", b"x" * 32769,
            b"\xff", b"this is not valid python!\n"):
            expected = hashlib.sha256(bad).hexdigest() if type(bad) is bytes else sha
            with self.subTest(kind=type(bad).__name__), self.assertRaises(ValueError):
                controller.task_spec(bad, expected)
        for bad in (None, True, "A" * 64, "0" * 64):
            with self.assertRaises(ValueError): controller.task_spec(source, bad)

    def test_task_is_one_fixed_secretless_command_without_source_context(self):
        selected = plan(); task = selected["task"]
        self.assertEqual("v1.1.0", task["version"]); self.assertEqual(60, task["stepTimeout"])
        self.assertEqual(1, len(task["steps"])); step = task["steps"][0]
        self.assertEqual({"id", "cmd", "entryPoint", "disableWorkingDirectoryOverride", "env", "timeout",
            "retries", "repeat", "privileged", "pull", "user"}, set(step))
        self.assertEqual("python", step["entryPoint"]); self.assertEqual([], step["env"])
        self.assertIs(step["disableWorkingDirectoryOverride"], True); self.assertIs(step["privileged"], False)
        self.assertIs(step["pull"], True); self.assertEqual("10001:10001", step["user"])
        self.assertEqual(0, step["retries"]); self.assertEqual(0, step["repeat"])
        self.assertEqual(controller.IMAGE, selected["image"])
        self.assertTrue(step["cmd"].startswith(controller.IMAGE + ' -I -B -c "'))
        self.assertEqual(controller.digest(task), selected["task_sha256"])
        self.assertFalse(selected["credentials_supplied"])
        self.assertEqual((0, 0), (selected["sql_calls"], selected["model_calls"]))
        for prohibited in ("identity", "secrets", "volumes", "volumeMounts", "build", "push", "workingDirectory"):
            self.assertNotIn(prohibited, task); self.assertNotIn(prohibited, step)

    def test_bundled_source_digest_checked_and_real_ambient_guard_called(self):
        selected = plan(); command = selected["task"]["steps"][0]["cmd"]
        packed = re.search(r"base64\.b64decode\('([A-Za-z0-9+/=]+)'\)", command).group(1)
        source = gzip.decompress(base64.b64decode(packed)).decode("utf-8")
        self.assertEqual(selected["probe_payload_sha256"], hashlib.sha256(source.encode()).hexdigest())
        tree = ast.parse(source)
        checks = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute) and node.func.attr == "check_runtime"]
        self.assertEqual(1, len(checks)); self.assertEqual([], checks[0].args); self.assertEqual([], checks[0].keywords)
        self.assertIn(selected["runtime_source_sha256"], source)
        self.assertIn("OWNER_TASK_PROVIDER_RUNTIME_FAILURE runtime_check_failed", source)
        self.assertNotIn("AzureCliCredential", source); self.assertNotIn("psycopg.connect", source)

    def test_probe_plan_rejects_source_changed_during_second_read(self):
        with patch.object(controller.Path if hasattr(controller, "Path") else Path, "read_bytes",
            side_effect=[b"pass\n", b"# changed\n"]):
            with self.assertRaisesRegex(ValueError, "changed_while_bundling"):
                controller.probe_plan()


class OutputParserTests(unittest.TestCase):
    def test_exact_native_descriptor_plain_or_engine_timestamp(self):
        selected = plan(); value = record(selected)
        for prefix in ("", "[2026-10-04 17:00:00Z] "):
            self.assertEqual(value, controller.parse_logs(logs(value, prefix=prefix), selected))

    def test_complete_log_bounds_native_bytes_utf8_and_null(self):
        selected = plan()
        for raw in (None, "text", bytearray(logs()), b"", b"x" * (controller.MAX_LOG_BYTES + 1), b"\xff",
            logs() + b"\x00"):
            with self.subTest(kind=type(raw).__name__), self.assertRaises(ValueError):
                controller.parse_logs(raw, selected)

    def test_foreign_binding_duplicate_records_and_invalid_descriptor(self):
        selected = plan()
        for field, changed in (("operation_id", "00000000-0000-0000-0000-000000000000"),
            ("image", controller.IMAGE.replace("ibkr-platform", "foreign")), ("runtime_source_sha256", "f" * 64)):
            value = record(selected); value[field] = changed
            with self.subTest(field=field), self.assertRaises(ValueError): controller.parse_logs(logs(value), selected)
        value = record(selected); value["runtime"]["tls_connection_verified"] = True
        with self.assertRaises(ValueError): controller.parse_logs(logs(value), selected)
        with self.assertRaises(ValueError): controller.parse_logs(logs() + logs(), selected)
        with self.assertRaises(ValueError): controller.parse_logs(logs(prefix="foreign-output "), selected)

    def test_duplicate_fields_non_native_and_extra_broad_protocol_markers_rejected(self):
        selected = plan(); raw = logs(selected_plan=selected)
        repeated = raw.replace(b'"schema_version":1', b'"schema_version":1,"schema_version":1')
        with self.assertRaises(ValueError): controller.parse_logs(repeated, selected)
        non_native = raw.replace(b'"schema_version":1', b'"schema_version":NaN')
        with self.assertRaises(ValueError): controller.parse_logs(non_native, selected)
        for marker in (b"OWNER_TASK_PROVIDER_RUNTIME_FAILURE runtime_check_failed\n",
            b"OWNER_TASK_PROVIDER_RUNTIME_V2 foreign record\n",
            b"OWNER_TASK_PROVIDER_RUNTIME foreign malformed\n"):
            with self.subTest(marker=marker), self.assertRaises(ValueError):
                controller.parse_logs(raw + marker, selected)

    def test_engine_digest_exactly_once_and_sensitive_full_log_rejected(self):
        selected = plan(); raw = logs(selected_plan=selected)
        image_digest = controller.IMAGE.split("@", 1)[1].encode()
        for changed in (raw.replace(b"Digest: " + image_digest, b"Digest: sha256:" + b"f" * 64),
            raw.replace(b"Digest: " + image_digest + b"\n", b""), raw + b"Digest: " + image_digest + b"\n"):
            with self.assertRaises(ValueError): controller.parse_logs(changed, selected)
        for sensitive in ("?sv=secret", "Authorization: Bearer secret", "postgresql://private",
            "AccountKey=private", "OWNERTRIAL_ADMIN_URL=private"):
            with self.subTest(sensitive=sensitive), self.assertRaisesRegex(ValueError, "sensitive_log"):
                controller.parse_logs(raw + sensitive.encode(), selected)


class CliTests(unittest.TestCase):
    def test_exact_dispatch_and_read_only_cli_shapes_no_injection(self):
        with self.assertRaises(ValueError): controller.cli_args("dispatch")
        for stage in ("show", "logs"):
            args = controller.cli_args(stage, "ab123")
            self.assertEqual(["acr", "task", "show-run" if stage == "show" else "logs"], args[:3])
            self.assertEqual("ab123", args[args.index("--run-id") + 1])
            self.assertNotIn("--file", args); self.assertNotIn("run", args)
        for value in (None, True, "", "ab123 --subscription foreign", "../ab123", "ab123;echo secret", "A123", "x" * 25):
            with self.subTest(value=value), self.assertRaises(ValueError): controller.cli_args("show", value)
        with self.assertRaises(ValueError): controller.cli_args("dispatch", "ab123")
        with self.assertRaises(ValueError): controller.cli_args("create")

    def test_call_cli_exact_frozen_task_bounded_no_shell_or_secret_args(self):
        selected = plan(); result = SimpleNamespace(returncode=0, stdout=b'{"runId":"ab123"}', stderr=b"")
        with patch.object(controller, "probe_plan", return_value=selected), \
            patch.object(controller.subprocess, "run", return_value=result) as called:
            self.assertEqual({"runId": "ab123"}, controller.call_cli("show", "ab123"))
        positional, keywords = called.call_args
        self.assertEqual(controller.cli_args("show", "ab123"), positional[0][3:])
        self.assertNotIn("input", keywords)
        self.assertEqual(90, keywords["timeout"]); self.assertEqual(controller.ROOT, keywords["cwd"])
        self.assertNotIn("shell", keywords); self.assertNotIn("env", keywords)
        changed = copy.deepcopy(selected["task"]); changed["steps"][0]["env"] = ["SECRET=value"]
        with patch.object(controller, "probe_plan", return_value=selected), patch.object(controller.subprocess, "run") as called:
            with self.assertRaises(ValueError): controller.call_cli("dispatch", task=changed)
            with self.assertRaises(ValueError): controller.call_cli("dispatch", task=selected["task"])
            called.assert_not_called()

    def test_cli_errors_timeout_oversize_duplicate_json_and_non_native_sanitized(self):
        results = [SimpleNamespace(returncode=1, stdout=b"", stderr=b"synthetic-private"),
            SimpleNamespace(returncode=0, stdout=b"x" * (controller.MAX_LOG_BYTES + 1), stderr=b""),
            SimpleNamespace(returncode=0, stdout=b'{"runId":"ab123","runId":"other"}', stderr=b""),
            SimpleNamespace(returncode=0, stdout=b'{"runId":"ab123","extra":NaN}', stderr=b"")]
        for result in results:
            with patch.object(controller.subprocess, "run", return_value=result), \
                self.subTest(stdout_size=len(result.stdout)), self.assertRaises(ValueError) as error:
                controller.call_cli("show", "ab123")
            self.assertNotIn("synthetic-private", str(error.exception))
        with patch.object(controller.subprocess, "run", side_effect=RuntimeError("synthetic-private")):
            with self.assertRaises(ValueError) as error: controller.call_cli("show", "ab123")
            self.assertEqual("runtime_cli_result_unverified_attempt_not_reissued", str(error.exception))

    def test_terminal_run_metadata_native_fixed_platform_and_no_dispatch(self):
        value = dict(runId="ab123", runType="QuickRun", status="Succeeded", platform={"os": "Linux", "architecture": "amd64"})
        with patch.object(controller, "call_cli", return_value=value) as call:
            self.assertEqual(value, controller.show("ab123")); call.assert_called_once_with("show", "ab123")
        for key, changed in (("runId", "other"), ("runType", "Build"), ("status", "Running"),
            ("platform", {"os": "Linux", "architecture": "arm64"}), ("platform", {"os": "Windows", "architecture": "amd64"})):
            invalid = dict(value); invalid[key] = changed
            with patch.object(controller, "call_cli", return_value=invalid), self.assertRaises(ValueError):
                controller.show("ab123")


class ScheduleTransportTests(unittest.TestCase):
    def response(self, *, status=200, raw=None, chunks=None):
        result = Mock()
        result.status_code = status
        result.iter_content.return_value = chunks if chunks is not None else [controller.canonical(http_ack()) if raw is None else raw]
        return result

    def credential(self):
        result = Mock()
        result.get_token.return_value = SimpleNamespace(token="synthetic-only-token")
        return result

    def test_exact_encoded_schedule_body_no_custom_identity_secrets_or_context(self):
        selected = plan()
        with patch.object(controller, "probe_plan", return_value=selected):
            body = controller.schedule_body(selected["task"])
        self.assertEqual({"type", "encodedTaskContent", "platform", "timeout", "agentConfiguration",
            "credentials", "isArchiveEnabled"}, set(body))
        self.assertEqual("EncodedTaskRunRequest", body["type"])
        self.assertEqual(selected["task"], json.loads(base64.b64decode(body["encodedTaskContent"], validate=True)))
        self.assertEqual({"os": "Linux", "architecture": "amd64"}, body["platform"])
        self.assertEqual(300, body["timeout"]); self.assertEqual({"cpu": 2}, body["agentConfiguration"])
        self.assertEqual({"sourceRegistry": {"loginMode": "Default"}}, body["credentials"])
        self.assertIs(body["isArchiveEnabled"], False)
        for prohibited in ("identity", "sourceLocation", "agentPoolName", "secrets", "environmentVariables", "values"):
            self.assertNotIn(prohibited, body)

    def test_changed_task_rejected_before_authentication_or_post(self):
        selected = plan(); credential = self.credential(); session = Mock()
        changes = [lambda value: value.update(extra=True),
            lambda value: value["steps"][0].update(env=["SECRET=private"]),
            lambda value: value["steps"][0].update(cmd="foreign"),
            lambda value: value["steps"][0].update(retries=True),
            lambda value: value["steps"][0].update(privileged=True)]
        with patch.object(controller, "probe_plan", return_value=selected):
            for change in changes:
                task = copy.deepcopy(selected["task"]); change(task)
                with self.assertRaises(ValueError): controller.dispatch_http(task, credential=credential, session=session)
        credential.get_token.assert_not_called(); session.post.assert_not_called()

    def test_one_post_exact_resource_body_audience_no_redirect_retry_or_poll(self):
        selected = plan(); response = self.response(); session = Mock(); session.post.return_value = response
        credential = self.credential()
        with patch.object(controller, "probe_plan", return_value=selected):
            result = controller.dispatch_http(selected["task"], credential=credential, session=session)
            body = controller.schedule_body(selected["task"])
        self.assertEqual(http_ack(), result)
        credential.get_token.assert_called_once_with("https://management.azure.com/.default")
        session.post.assert_called_once_with(controller.SCHEDULE_URL,
            headers={"Authorization": "Bearer synthetic-only-token"}, json=body,
            allow_redirects=False, timeout=(5, 30), stream=True)
        self.assertEqual("https://management.azure.com" + controller.canary.TARGET.registry_resource_id
            + "/scheduleRun?api-version=2019-04-01", controller.SCHEDULE_URL)
        session.get.assert_not_called(); response.close.assert_called_once(); session.close.assert_not_called()

    def test_owned_session_disables_proxy_environment_and_http_retries(self):
        selected = plan(); response = self.response(); session = Mock(); session.post.return_value = response
        credential = self.credential()
        with patch.object(controller, "probe_plan", return_value=selected), \
            patch.object(controller.requests, "Session", return_value=session):
            self.assertEqual(http_ack(), controller.dispatch_http(selected["task"], credential=credential))
        self.assertIs(session.trust_env, False)
        session.mount.assert_called_once(); self.assertEqual("https://", session.mount.call_args.args[0])
        self.assertEqual(0, session.mount.call_args.args[1].max_retries.total)
        response.close.assert_called_once(); session.close.assert_called_once()

    def test_redirect_202_failure_and_non_native_status_are_unknown_without_retry(self):
        selected = plan()
        for status in (202, 201, 301, 302, 400, 401, 403, 500, True, "200"):
            response = self.response(status=status); session = Mock(); session.post.return_value = response
            with patch.object(controller, "probe_plan", return_value=selected), self.subTest(status=status), \
                self.assertRaisesRegex(ValueError, "attempt_consumed_inspect_only"):
                controller.dispatch_http(selected["task"], credential=self.credential(), session=session)
            session.post.assert_called_once(); response.close.assert_called_once(); session.get.assert_not_called()

    def test_lost_ack_stream_failure_oversize_and_non_bytes_never_retry(self):
        selected = plan()
        for failure in ("post", "stream", "oversize", "not-bytes", "empty"):
            response = self.response(); session = Mock(); session.post.return_value = response
            if failure == "post": session.post.side_effect = RuntimeError("synthetic-private")
            elif failure == "stream": response.iter_content.side_effect = RuntimeError("synthetic-private")
            elif failure == "oversize": response.iter_content.return_value = [b"x" * (controller.MAX_LOG_BYTES + 1)]
            elif failure == "not-bytes": response.iter_content.return_value = ["synthetic-private"]
            else: response.iter_content.return_value = []
            with patch.object(controller, "probe_plan", return_value=selected), self.subTest(failure=failure), \
                self.assertRaises(ValueError) as error:
                controller.dispatch_http(selected["task"], credential=self.credential(), session=session)
            self.assertEqual("runtime_schedule_ack_unknown_attempt_consumed_inspect_only", str(error.exception))
            session.post.assert_called_once(); session.get.assert_not_called()
            self.assertNotIn("synthetic-private", str(error.exception))
            if failure != "post": response.close.assert_called_once()

    def test_duplicate_non_native_and_foreign_acknowledgements_refused(self):
        selected = plan(); correct = http_ack(); malformed = []
        for key, value in (("id", "/foreign/runs/ab123"), ("name", "other"), ("properties", {"runId": True}),
            ("properties", {"runId": "ab123; injected"}), ("properties", [])):
            changed = dict(correct); changed[key] = value; malformed.append(controller.canonical(changed))
        malformed.extend((b'[]', b'null', b'{"name":"ab123","name":"other","properties":{"runId":"ab123"}}',
            b'{"properties":{"runId":"ab123"},"unexpected":NaN}', b'\xff'))
        for raw in malformed:
            response = self.response(raw=raw); session = Mock(); session.post.return_value = response
            with patch.object(controller, "probe_plan", return_value=selected), self.subTest(raw=raw), self.assertRaises(ValueError):
                controller.dispatch_http(selected["task"], credential=self.credential(), session=session)
            session.post.assert_called_once(); response.close.assert_called_once()

    def test_cleanup_uncertainty_is_sanitized_consumed_and_never_retried(self):
        selected = plan()
        for stage in ("response-close", "owned-session-close"):
            response = self.response(); session = Mock(); session.post.return_value = response
            if stage == "response-close": response.close.side_effect = RuntimeError("synthetic-private")
            else: session.close.side_effect = RuntimeError("synthetic-private")
            with patch.object(controller, "probe_plan", return_value=selected), \
                patch.object(controller.requests, "Session", return_value=session), self.subTest(stage=stage), \
                self.assertRaises(ValueError) as error:
                controller.dispatch_http(selected["task"], credential=self.credential())
            self.assertEqual("runtime_schedule_close_unverified_attempt_consumed_inspect_only", str(error.exception))
            self.assertNotIn("synthetic-private", str(error.exception)); session.post.assert_called_once()
            session.get.assert_not_called()


class DispatchAndCollectionTests(unittest.TestCase):
    def test_protected_reservation_intent_and_readback_precede_one_dispatch(self):
        events = []; selected = plan(); prepared = prepared_value(selected)
        raw = dict(prepared, protected_receipt=receipt("preflight"))
        def save(value, name):
            events.append("save:" + name); return dict(value, protected_receipt=receipt(name))
        def call(task):
            events.append("dispatch-http"); self.assertEqual(selected["task"], task)
            return http_ack()
        with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
            patch.object(controller, "load", return_value=(raw, prepared)), \
            patch.object(controller, "frozen", side_effect=lambda value: events.append("frozen")), \
            patch.object(controller, "reserve", side_effect=lambda value: events.append("reserve") or receipt("reservation")), \
            patch.object(controller, "save", side_effect=save), \
            patch.object(controller, "verify_reservation", side_effect=lambda *args: events.append("verify-reservation")), \
            patch.object(controller, "dispatch_http", side_effect=call) as called, \
            patch.object(controller, "call_cli") as cli:
            result = controller.dispatch()
        self.assertEqual("ab123", result["run_id"]); called.assert_called_once(); cli.assert_not_called()
        self.assertEqual(["frozen", "reserve", "save:dispatch-intent.json", "frozen", "verify-reservation",
            "dispatch-http", "save:dispatch-ack.json"], events)

    def test_lost_ack_consumes_intent_and_cannot_redispatch(self):
        selected = plan(); prepared = prepared_value(selected); raw = dict(prepared, protected_receipt=receipt("preflight"))
        with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
            patch.object(controller, "load", return_value=(raw, prepared)), patch.object(controller, "frozen"), \
            patch.object(controller, "reserve", return_value=receipt("reservation")) as reserved, \
            patch.object(controller, "verify_reservation"), \
            patch.object(controller, "dispatch_http", side_effect=ValueError("unknown_ack_no_retry")) as called:
            def save(value, name):
                (Path(directory) / name).touch(exist_ok=False)
                return dict(value, protected_receipt=receipt(name))
            with patch.object(controller, "save", side_effect=save):
                with self.assertRaisesRegex(ValueError, "effect_unknown_attempt_consumed"): controller.dispatch()
                with self.assertRaisesRegex(ValueError, "consumed"): controller.dispatch()
            called.assert_called_once(); reserved.assert_called_once()

    def test_unverified_dispatch_retains_only_fixed_unknown_outcome_and_cannot_rearm(self):
        selected = plan(); prepared = prepared_value(selected); raw = dict(prepared, protected_receipt=receipt("preflight"))
        cases = (("transport", RuntimeError("synthetic-private response detail"), None),
            ("ack-not-native", None, []), ("ack-run-invalid", None, {"properties": {"runId": True}}))
        for case, error, acknowledgement in cases:
            retained = []
            with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
                patch.object(controller, "load", return_value=(raw, prepared)), patch.object(controller, "frozen"), \
                patch.object(controller, "reserve", return_value=receipt("reservation")) as reserve, \
                patch.object(controller, "verify_reservation"), \
                patch.object(controller, "dispatch_http", side_effect=error, return_value=acknowledgement) as dispatched:
                def save(value, name):
                    (Path(directory) / name).touch(exist_ok=False)
                    retained.append((name, copy.deepcopy(value)))
                    return dict(value, protected_receipt=receipt(name))
                with patch.object(controller, "save", side_effect=save):
                    with self.subTest(case=case), self.assertRaises(ValueError) as failure:
                        controller.dispatch()
                    self.assertEqual("runtime_dispatch_effect_unknown_attempt_consumed_inspect_only", str(failure.exception))
                    with self.assertRaisesRegex(ValueError, "consumed"): controller.dispatch()
                dispatched.assert_called_once(); reserve.assert_called_once()
            self.assertEqual(["dispatch-intent.json", "dispatch-outcome.json"], [name for name, _ in retained])
            outcome = retained[-1][1]
            self.assertEqual({"status", "operation_id", "intent_receipt", "reservation_receipt", "attempt_consumed",
                "automatic_retry", "no_effects_verified", "credentials_supplied", "sql_calls", "model_calls"}, set(outcome))
            self.assertEqual("effect_unknown_ack_unverified_inspect_only", outcome["status"])
            self.assertEqual(controller.OPERATION_ID, outcome["operation_id"])
            self.assertEqual(receipt("dispatch-intent.json"), outcome["intent_receipt"])
            self.assertEqual(receipt("reservation"), outcome["reservation_receipt"])
            self.assertIs(outcome["attempt_consumed"], True); self.assertIs(outcome["automatic_retry"], False)
            self.assertIs(outcome["no_effects_verified"], False); self.assertIs(outcome["credentials_supplied"], False)
            self.assertEqual((0, 0), (outcome["sql_calls"], outcome["model_calls"]))
            self.assertNotIn("synthetic-private", controller.canonical(outcome).decode())

    def test_unknown_outcome_retention_failure_preserves_consumed_intent_without_repeat_post(self):
        prepared = prepared_value(); raw = dict(prepared, protected_receipt=receipt("preflight"))
        retained = []
        with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
            patch.object(controller, "load", return_value=(raw, prepared)), patch.object(controller, "frozen"), \
            patch.object(controller, "reserve", return_value=receipt("reservation")) as reserve, \
            patch.object(controller, "verify_reservation"), \
            patch.object(controller, "dispatch_http", side_effect=RuntimeError("synthetic-private-ack")) as dispatched:
            def save(value, name):
                if name == "dispatch-outcome.json":
                    raise RuntimeError("synthetic-private-retention-error")
                (Path(directory) / name).touch(exist_ok=False)
                retained.append((name, copy.deepcopy(value)))
                return dict(value, protected_receipt=receipt(name))
            with patch.object(controller, "save", side_effect=save):
                with self.assertRaises(ValueError) as failure: controller.dispatch()
                self.assertEqual("runtime_dispatch_outcome_unretained_attempt_consumed_inspect_only", str(failure.exception))
                self.assertNotIn("synthetic-private", str(failure.exception))
                self.assertTrue((Path(directory) / "dispatch-intent.json").exists())
                self.assertFalse((Path(directory) / "dispatch-ack.json").exists())
                with self.assertRaisesRegex(ValueError, "consumed"): controller.dispatch()
            dispatched.assert_called_once(); reserve.assert_called_once()
        self.assertEqual(["dispatch-intent.json"], [name for name, _ in retained])
        self.assertEqual(receipt("reservation"), retained[0][1]["reservation_receipt"])

    def test_reservation_uncertainty_or_intent_failure_never_dispatches(self):
        prepared = prepared_value(); raw = dict(prepared, protected_receipt=receipt("preflight"))
        for failed_stage in ("reserve", "save", "verify"):
            with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
                patch.object(controller, "load", return_value=(raw, prepared)), patch.object(controller, "frozen"), \
                patch.object(controller, "reserve", side_effect=ValueError("unknown-reservation") if failed_stage == "reserve" else None,
                    return_value=receipt("reservation")), \
                patch.object(controller, "save", side_effect=ValueError("uncertain-intent") if failed_stage == "save" else None,
                    return_value={"protected_receipt": receipt("intent")}), \
                patch.object(controller, "verify_reservation", side_effect=ValueError("unverified") if failed_stage == "verify" else None), \
                patch.object(controller, "dispatch_http") as call:
                with self.subTest(failed_stage=failed_stage), self.assertRaises(ValueError): controller.dispatch()
                call.assert_not_called()

    def test_collect_only_reads_run_logs_and_saves_complete_output_once(self):
        values, prepared, _, _ = collect_fixture(); raw = logs(selected_plan=prepared["plan"])
        shown = {"runId": "ab123", "status": "Succeeded"}
        with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
            patch.object(controller, "load", side_effect=lambda name: values[name]), patch.object(controller, "frozen"), \
            patch.object(controller, "verify_reservation"), patch.object(controller, "reserve") as reserve, \
            patch.object(controller, "show", return_value=shown) as show, \
            patch.object(controller, "call_cli", return_value=raw) as call, \
            patch.object(controller, "save", side_effect=lambda value, name: dict(value, protected_receipt=receipt(name))) as save:
            result = controller.collect()
        self.assertEqual(2, show.call_count); call.assert_called_once_with("logs", "ab123"); reserve.assert_not_called()
        save.assert_called_once(); self.assertEqual("acceptance.json", save.call_args.args[1])
        self.assertEqual(raw.decode(), result["full_logs"]); self.assertEqual(len(raw), result["full_logs_bytes"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), result["full_logs_sha256"])
        self.assertEqual(shown, result["raw_run_before"]); self.assertEqual(shown, result["raw_run_after"])
        for flag in controller.FALSE_FLAGS: self.assertIs(result[flag], False)

    def test_fresh_verify_does_not_write_reserve_dispatch_or_create_folder(self):
        values, prepared, _, _ = collect_fixture(); raw = logs(selected_plan=prepared["plan"])
        shown = {"runId": "ab123", "status": "Succeeded"}
        with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
            patch.object(controller, "load", side_effect=lambda name: values[name]), patch.object(controller, "frozen"), \
            patch.object(controller, "verify_reservation"), patch.object(controller, "reserve") as reserve, \
            patch.object(controller, "show", return_value=shown), patch.object(controller, "call_cli", return_value=raw) as call, \
            patch.object(controller, "save", side_effect=lambda value, name: value) as save:
            accepted = controller.collect()
            values["acceptance.json"] = (dict(accepted, protected_receipt=receipt("acceptance")), accepted)
            save.reset_mock()
            with patch.object(Path, "mkdir") as mkdir:
                verified = controller.collect(True)
            save.assert_not_called(); reserve.assert_not_called(); mkdir.assert_not_called()
            self.assertTrue(all(item.args == ("logs", "ab123") for item in call.call_args_list))
        self.assertEqual("independent_secretless_candidate_runtime_verified", verified["status"])
        self.assertEqual(receipt("acceptance"), verified["protected_receipt"])

    def test_collect_refuses_invalid_status_or_raw_ack_run_chain(self):
        for mutation in ("intent-status", "ack-status", "raw-run-id", "intent-receipt", "operation-id"):
            values, _, intent, ack = collect_fixture()
            if mutation == "intent-status": intent["status"] = "foreign-intent"
            elif mutation == "ack-status": ack["status"] = "foreign-ack"
            elif mutation == "raw-run-id": ack["raw_ack"]["properties"]["runId"] = "foreign"
            elif mutation == "intent-receipt": ack["intent_receipt"] = receipt("wrong")
            else: ack["operation_id"] = "foreign"
            with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
                patch.object(controller, "load", side_effect=lambda name: values[name]), patch.object(controller, "frozen"), \
                patch.object(controller, "verify_reservation"), patch.object(controller, "show") as show, \
                patch.object(controller, "call_cli") as call, patch.object(controller, "save") as save:
                with self.subTest(mutation=mutation), self.assertRaises(ValueError): controller.collect()
                show.assert_not_called(); call.assert_not_called(); save.assert_not_called()

    def test_run_drift_or_retained_mismatch_never_saves_acceptance(self):
        values, prepared, _, _ = collect_fixture(); raw = logs(selected_plan=prepared["plan"])
        with TemporaryDirectory() as directory, patch.object(controller, "FOLDER", Path(directory)), \
            patch.object(controller, "load", side_effect=lambda name: values[name]), patch.object(controller, "frozen"), \
            patch.object(controller, "verify_reservation"), patch.object(controller, "show", side_effect=[{"id": 1}, {"id": 2}]), \
            patch.object(controller, "call_cli", return_value=raw), patch.object(controller, "save") as save:
            with self.assertRaisesRegex(ValueError, "drift_during_collection"): controller.collect()
            save.assert_not_called()

    def test_module_has_no_import_time_cli_or_cloud_side_effect(self):
        tree = ast.parse(Path(controller.__file__).read_text(encoding="utf-8"))
        for node in tree.body:
            if isinstance(node, ast.If):
                self.assertEqual("__name__ == '__main__'", ast.unparse(node.test))
            elif isinstance(node, ast.Expr):
                self.assertIsInstance(node.value, ast.Constant)


if __name__ == "__main__":
    unittest.main()
