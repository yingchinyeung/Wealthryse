from __future__ import annotations

import json
import unittest
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

import urllib.error

from ibkr_etl.admin_actions import (
    ACTION_REGISTRY,
    AdminActionConfig,
    AdminActionError,
    claim_action_for_execution,
    configured_env,
    create_action_preview,
    execute_claimed_action,
    execution_template,
    start_job_with_env,
)


class _Cursor:
    def __init__(self, row=None, rowcount: int = 1) -> None:
        self._row = row
        self.rowcount = rowcount

    def fetchone(self):
        return self._row


class _Connection:
    def __init__(self) -> None:
        self.rows: dict[int, dict] = {}
        self.commits = 0
        self.rollbacks = 0

    def execute(self, sql: str, parameters=()):
        normalized = " ".join(sql.split())
        if normalized.startswith("INSERT INTO approved_actions"):
            approval_id = len(self.rows) + 1
            (
                correlation_id,
                action_type,
                payload_hash,
                payload_json,
                preview_json,
                requested_by,
                requested_at,
                expires_at,
            ) = parameters
            self.rows[approval_id] = {
                "approval_id": approval_id,
                "correlation_id": correlation_id,
                "action_type": action_type,
                "action_payload_sha256": payload_hash,
                "action_payload_json": payload_json,
                "preview_json": preview_json,
                "requested_by": requested_by,
                "requested_at": requested_at,
                "expires_at": expires_at,
                "status": "pending",
            }
            return _Cursor({"approval_id": approval_id})
        if normalized.startswith("SELECT approval_id"):
            return _Cursor(self.rows.get(int(parameters[0])))
        if "SET status = 'expired'" in normalized:
            self.rows[int(parameters[1])]["status"] = "expired"
            return _Cursor(rowcount=1)
        if "SET status = 'executing'" in normalized:
            row = self.rows[int(parameters[2])]
            if row["status"] != "pending":
                return _Cursor(rowcount=0)
            row["status"] = "executing"
            row["decided_by"] = parameters[0]
            return _Cursor(rowcount=1)
        if "SET status = 'failed'" in normalized:
            row = self.rows[int(parameters[1])]
            row["status"] = "failed"
            row["failure_code"] = parameters[0]
            return _Cursor(rowcount=1)
        if "SET status = 'executed'" in normalized:
            row = self.rows[int(parameters[2])]
            row["status"] = "executed"
            row["result_reference"] = parameters[1]
            return _Cursor(rowcount=1)
        raise AssertionError(f"Unexpected SQL: {normalized}")

    def commit(self) -> None:
        self.commits += 1

    def rollback(self) -> None:
        self.rollbacks += 1


class AdminActionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = AdminActionConfig(
            enabled=True,
            environment="staging",
            subscription_id="subscription-id",
            resource_group="rg-ibkr-database-v11",
            managed_identity_client_id="identity-id",
        )
        self.now = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)

    def _preview(self, connection: _Connection, action_type="refresh_risk_free_rate"):
        return create_action_preview(
            connection,
            self.config,
            action_type=action_type,
            actor_object_id="owner-object-id",
            correlation_id="correlation-id",
            now=self.now,
        )

    def test_registry_contains_only_two_fixed_staging_jobs(self) -> None:
        self.assertEqual(
            set(ACTION_REGISTRY),
            {"refresh_risk_free_rate", "refresh_provisional_benchmarks"},
        )
        serialized = json.dumps({key: value.target_job for key, value in ACTION_REGISTRY.items()})
        self.assertIn("ibkrv2stg-risk-free-daily", serialized)
        self.assertIn("ibkrv2stg-benchmark-daily", serialized)
        self.assertNotIn("ibkr-v11-daily", serialized)

    def test_non_staging_or_disabled_configuration_fails_closed(self) -> None:
        for config, code in (
            (AdminActionConfig(False, "staging", "s", "r", "i"), "admin_actions_disabled"),
            (AdminActionConfig(True, "production", "s", "r", "i"), "production_actions_prohibited"),
        ):
            with self.subTest(code=code), self.assertRaises(AdminActionError) as raised:
                config.validate()
            self.assertEqual(raised.exception.code, code)

    def test_preview_is_integrity_bound_short_lived_and_excludes_production(self) -> None:
        connection = _Connection()
        preview = self._preview(connection)
        stored = connection.rows[preview["approval_id"]]
        self.assertEqual(preview["status"], "pending")
        self.assertEqual(preview["expires_at"], (self.now + timedelta(minutes=10)).isoformat())
        self.assertEqual(len(preview["action_payload_sha256"]), 64)
        self.assertEqual(stored["action_payload_sha256"], preview["action_payload_sha256"])
        self.assertNotIn("ibkr-v11", stored["action_payload_json"])
        self.assertIn("production changes", preview["preview"]["does_not_allow"])

    def test_claim_requires_same_actor_digest_phrase_and_pending_status(self) -> None:
        connection = _Connection()
        preview = self._preview(connection)
        for kwargs, code in (
            ({"actor_object_id": "other"}, "approval_actor_mismatch"),
            ({"expected_payload_sha256": "0" * 64}, "approval_digest_mismatch"),
            ({"confirmation": "APPROVE SOMETHING ELSE"}, "confirmation_mismatch"),
        ):
            values = {
                "approval_id": preview["approval_id"],
                "expected_payload_sha256": preview["action_payload_sha256"],
                "confirmation": preview["confirmation_phrase"],
                "actor_object_id": "owner-object-id",
                "correlation_id": "approval-correlation",
                "now": self.now,
            }
            values.update(kwargs)
            with self.subTest(code=code), self.assertRaises(AdminActionError) as raised:
                claim_action_for_execution(connection, self.config, **values)
            self.assertEqual(raised.exception.code, code)
        definition, payload = claim_action_for_execution(
            connection,
            self.config,
            approval_id=preview["approval_id"],
            expected_payload_sha256=preview["action_payload_sha256"],
            confirmation=preview["confirmation_phrase"],
            actor_object_id="owner-object-id",
            correlation_id="approval-correlation",
            now=self.now,
        )
        self.assertEqual(definition.target_job, "ibkrv2stg-risk-free-daily")
        self.assertEqual(payload["environment"], "staging")
        with self.assertRaises(AdminActionError) as raised:
            claim_action_for_execution(
                connection,
                self.config,
                approval_id=preview["approval_id"],
                expected_payload_sha256=preview["action_payload_sha256"],
                confirmation=preview["confirmation_phrase"],
                actor_object_id="owner-object-id",
                correlation_id="approval-correlation",
                now=self.now,
            )
        self.assertEqual(raised.exception.code, "approval_not_pending")

    def test_expired_preview_cannot_execute(self) -> None:
        connection = _Connection()
        preview = self._preview(connection)
        with self.assertRaises(AdminActionError) as raised:
            claim_action_for_execution(
                connection,
                self.config,
                approval_id=preview["approval_id"],
                expected_payload_sha256=preview["action_payload_sha256"],
                confirmation=preview["confirmation_phrase"],
                actor_object_id="owner-object-id",
                correlation_id="approval-correlation",
                now=self.now + timedelta(minutes=11),
            )
        self.assertEqual(raised.exception.code, "approval_expired")
        self.assertEqual(connection.rows[preview["approval_id"]]["status"], "expired")

    def test_execution_uses_registry_target_and_marks_single_result(self) -> None:
        connection = _Connection()
        preview = self._preview(connection)
        definition, _ = claim_action_for_execution(
            connection,
            self.config,
            approval_id=preview["approval_id"],
            expected_payload_sha256=preview["action_payload_sha256"],
            confirmation=preview["confirmation_phrase"],
            actor_object_id="owner-object-id",
            correlation_id="approval-correlation",
            now=self.now,
        )

        @contextmanager
        def factory():
            yield connection

        starts: list[str] = []

        def starter(config, job_name):
            starts.append(job_name)
            return "/executions/test-execution"

        result = execute_claimed_action(
            factory,
            self.config,
            approval_id=preview["approval_id"],
            definition=definition,
            actor_object_id="owner-object-id",
            correlation_id="approval-correlation",
            payload_sha256=preview["action_payload_sha256"],
            job_starter=starter,
        )
        self.assertEqual(starts, ["ibkrv2stg-risk-free-daily"])
        self.assertEqual(result["status"], "executed")
        self.assertEqual(connection.rows[preview["approval_id"]]["status"], "executed")


def _job(**container) -> dict:
    """A synthetic job as Azure's GET returns it."""
    main = {
        "name": "derived",
        "image": "example.azurecr.io/ibkr:synthetic",
        "command": ["python"],
        "args": ["scripts/run_derived.py"],
        "env": [{"name": "INPUT_READINESS_MODE", "value": "enforce"},
                {"name": "DB_PASSWORD", "secretRef": "db-password"}],
        "resources": {"cpu": 0.5, "memory": "1Gi", "ephemeralStorage": "2Gi"},
    }
    main.update(container)
    return {"name": "ibkrv2stg-derived-daily",
            "properties": {"template": {"containers": [main], "initContainers": None, "volumes": []}}}


class _Response:
    def __init__(self, body: bytes, headers: dict | None = None) -> None:
        self._body = body
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class _Azure:
    """Answers the GET and start calls in order; an int answer raises that HTTP error."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.requests: list[tuple[str, str, object]] = []

    def __call__(self, request, timeout=None):
        body = json.loads(request.data) if request.data else None
        self.requests.append((request.get_method(), request.full_url, body))
        answer = self.answers.pop(0)
        if isinstance(answer, BaseException):
            raise answer
        if isinstance(answer, int):
            raise urllib.error.HTTPError(request.full_url, answer, "synthetic", {}, None)
        return _Response(json.dumps(answer).encode("utf-8"))


class StartJobWithEnvTests(unittest.TestCase):
    SHADOW = {"INPUT_READINESS_MODE": "shadow"}

    def setUp(self) -> None:
        self.config = AdminActionConfig(True, "staging", "subscription-id", "rg-synthetic", "identity-id")

    def _start(self, azure: _Azure, **options) -> str:
        return start_job_with_env(self.config, "ibkrv2stg-derived-daily", self.SHADOW,
                                  token=lambda: "synthetic-token", urlopen=azure, **options)

    def test_template_is_copied_with_the_env_set_for_that_execution(self) -> None:
        template = execution_template(_job(), self.SHADOW)
        container = template["containers"][0]
        self.assertEqual(container["env"], [{"name": "DB_PASSWORD", "secretRef": "db-password"},
                                            {"name": "INPUT_READINESS_MODE", "value": "shadow"}])
        self.assertEqual(container["resources"], {"cpu": 0.5, "memory": "1Gi"})
        self.assertEqual((container["image"], container["args"]),
                         ("example.azurecr.io/ibkr:synthetic", ["scripts/run_derived.py"]))
        self.assertEqual(set(template), {"containers"})

    def test_templates_it_cannot_copy_exactly_are_refused(self) -> None:
        self.assertIsNone(execution_template(_job(volumeMounts=[{"volumeName": "data"}]), self.SHADOW))
        self.assertIsNone(execution_template(_job(probes=[{"type": "Liveness"}]), self.SHADOW))
        with_volume = _job()
        with_volume["properties"]["template"]["volumes"] = [{"name": "data"}]
        self.assertIsNone(execution_template(with_volume, self.SHADOW))
        self.assertIsNone(execution_template({}, self.SHADOW))

    def test_start_posts_the_template_with_the_env(self) -> None:
        azure = _Azure(_job(), {"id": "execution-1"})
        self.assertEqual(self._start(azure), "execution-1")
        (get, get_url, _), (post, post_url, body) = azure.requests
        self.assertEqual((get, post), ("GET", "POST"))
        self.assertTrue(get_url.endswith("/jobs/ibkrv2stg-derived-daily?api-version=2024-03-01"))
        self.assertTrue(post_url.endswith("/jobs/ibkrv2stg-derived-daily/start?api-version=2024-03-01"))
        self.assertIn({"name": "INPUT_READINESS_MODE", "value": "shadow"}, body["containers"][0]["env"])

    def test_when_not_needed_the_job_starts_exactly_as_configured(self) -> None:
        self.assertEqual(configured_env(_job()), [{"INPUT_READINESS_MODE": "enforce", "DB_PASSWORD": None}])
        seen = []

        def needed(own):
            seen.append(dict(own))
            return own.get("INPUT_READINESS_MODE") == "enforce"

        azure = _Azure(_job(env=[{"name": "INPUT_READINESS_MODE", "value": "off"}]), {"id": "execution-4"})
        self.assertEqual(self._start(azure, needed=needed), "execution-4")
        self.assertEqual([(method, body) for method, _, body in azure.requests], [("GET", None), ("POST", {})])
        self.assertEqual(seen, [{"INPUT_READINESS_MODE": "off"}])
        azure = _Azure(_job(), {"id": "execution-5"})
        self.assertEqual(self._start(azure, needed=needed), "execution-5")
        self.assertIn({"name": "INPUT_READINESS_MODE", "value": "shadow"}, azure.requests[1][2]["containers"][0]["env"])

    def test_it_starts_the_job_as_configured_when_the_template_is_unusable(self) -> None:
        for first in (403, urllib.error.URLError("synthetic"), _job(volumeMounts=[{"volumeName": "data"}])):
            with self.subTest(first=first):
                azure = _Azure(first, {"name": "execution-2"})
                with self.assertLogs("uvicorn.error.ibkr_admin_action", "WARNING"):
                    self.assertEqual(self._start(azure), "execution-2")
                self.assertEqual([(method, body) for method, _, body in azure.requests][1:], [("POST", {})])

    def test_a_refused_template_falls_back_to_a_plain_start(self) -> None:
        azure = _Azure(_job(), 400, {"id": "execution-3"})
        with self.assertLogs("uvicorn.error.ibkr_admin_action", "WARNING"):
            self.assertEqual(self._start(azure), "execution-3")
        self.assertEqual([body for _, _, body in azure.requests][2], {})

    def test_server_errors_and_timeouts_are_reported_not_retried(self) -> None:
        cases = ((_job(), 503), (_job(), TimeoutError()), (403, 500))
        codes = ("azure_job_start_failed", "azure_job_start_unavailable", "azure_job_start_failed")
        for answers, code in zip(cases, codes):
            with self.subTest(code=code):
                azure = _Azure(*answers)
                with self.assertLogs("uvicorn.error.ibkr_admin_action", "WARNING") if answers[0] == 403 \
                        else _quiet():
                    with self.assertRaises(AdminActionError) as raised:
                        self._start(azure)
                self.assertEqual((raised.exception.status_code, raised.exception.code), (502, code))
                self.assertEqual(len(azure.requests), 2)


@contextmanager
def _quiet():
    yield


if __name__ == "__main__":
    unittest.main()
