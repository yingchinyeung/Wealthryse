"""The stage 1 rollout script: what it sends to Azure, without calling Azure."""
from __future__ import annotations

import base64
import contextlib
import gzip
import hashlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import live_data_stage1_payload as payload  # noqa: E402
import rollout_live_data_stage1 as rollout  # noqa: E402

PASSWORD = "stage1-test-only-password-aaaaaaaaaaaaaaaaaaaaaaaa"  # synthetic
IDENTITY = rollout.IDENTITY_PREFIX + "ibkrv2stg-ingestion-identity"


def _job(**overrides):
    container = {"name": "postgres-bootstrap", "image": rollout.recorded_image(), "command": ["python"],
                 "args": ["scripts/bootstrap_postgres.py"], "resources": {"cpu": 0.5, "memory": "1Gi"},
                 "env": [{"name": "DATABASE_ADMIN_URL", "secretRef": "database-admin-url"},
                         {"name": "DATABASE_APP_PASSWORD", "secretRef": "database-app-password"}],
                 "volumeMounts": []}
    job = {
        "identity": {"type": "UserAssigned", "userAssignedIdentities": {IDENTITY: {}}},
        "properties": {
            "configuration": {
                "triggerType": "Manual", "replicaRetryLimit": 0,
                "secrets": [{"name": "database-admin-url", "identity": IDENTITY,
                             "keyVaultUrl": f"https://{rollout.VAULT}.vault.azure.net/secrets/database-admin-url"}],
            },
            "template": {"containers": [container]},
        },
    }
    for path, value in overrides.items():
        target = job
        keys = path.split(".")
        for key in keys[:-1]:
            target = target[key] if not key.isdigit() else target[int(key)]
        target[keys[-1]] = value
    return job


def _source(args: list[str]) -> str:
    packed = args[1].split("b64decode('")[1].split("')")[0]
    return gzip.decompress(base64.b64decode(packed)).decode()


class BundleTests(unittest.TestCase):
    def test_bundle_carries_every_migration_checksum_and_only_063_text(self):
        value = rollout.bundle()
        names = sorted(p.name for p in (ROOT / "migrations" / "postgres").glob("*.sql"))
        self.assertEqual(sorted(value["expected"]), names)
        text = (ROOT / "migrations" / "postgres" / rollout.TARGET).read_text(encoding="utf-8")
        self.assertEqual(value["target_sql"], text)
        self.assertEqual(value["expected"][rollout.TARGET], hashlib.sha256(text.encode()).hexdigest())
        self.assertEqual((value["role"], value["web_role"], value["database"]),
                         ("ibkr_live_collector", "ibkr_stg_web", "ibkr_staging"))

    def test_command_is_deterministic_and_runs_the_payload(self):
        value = rollout.bundle()
        args = rollout.command(value)
        self.assertEqual(args, rollout.command(value))
        source = _source(args)
        self.assertNotIn("BUNDLE = None", source)
        self.assertTrue(source.rstrip().endswith("raise SystemExit(main())"))
        namespace = {"__name__": "stage1_check"}
        exec(compile(source.replace("raise SystemExit(main())", ""), "stage1", "exec"), namespace)
        self.assertEqual(namespace["decode"](namespace["BUNDLE"]), value)


class SecretHandlingTests(unittest.TestCase):
    def test_verifier_has_postgres_scram_form_and_hides_the_password(self):
        verifier = rollout.scram_verifier(PASSWORD)
        self.assertRegex(verifier, payload.VERIFIER)
        self.assertNotIn(PASSWORD, verifier)
        self.assertNotEqual(verifier, rollout.scram_verifier(PASSWORD))  # fresh salt each time
        salt = b"0123456789abcdef"
        self.assertEqual(rollout.scram_verifier(PASSWORD, salt), rollout.scram_verifier(PASSWORD, salt))

    def test_collector_url_round_trip_and_rejections(self):
        url = rollout.collector_url(PASSWORD + "/:@")
        self.assertEqual(rollout.collector_password(url), PASSWORD + "/:@")
        for wrong in (url.replace("ibkr_live_collector", "ibkr_stg_app"),
                      url.replace(rollout.HOST, "elsewhere.example"),
                      url.replace("/ibkr_staging", "/postgres"),
                      rollout.collector_url("short")):
            with self.assertRaises(SystemExit):
                rollout.collector_password(wrong)

    def test_template_keeps_the_admin_binding_and_sends_only_the_verifier(self):
        container = rollout.validate_job(_job(), rollout.recorded_image())
        verifier = rollout.scram_verifier(PASSWORD)
        value = rollout.template(container, ["-c", "pass"], verifier)["containers"][0]
        self.assertEqual(value["image"], rollout.recorded_image())
        self.assertEqual(value["env"], [{"name": "DATABASE_ADMIN_URL", "secretRef": "database-admin-url"},
                                        {"name": "LIVE_COLLECTOR_VERIFIER", "value": verifier}])
        self.assertNotIn("volumeMounts", value)
        self.assertNotIn(PASSWORD, json.dumps(value))

    def test_start_never_prints_or_sends_the_password(self):
        calls = []

        def fake(method, url, *, data=None, audience=rollout.MANAGEMENT, missing=False):
            calls.append((method, url, data))
            if url.startswith("https://" + rollout.VAULT):
                if method == "PUT":
                    fake.secret = data["value"]
                    return {}
                return None if not hasattr(fake, "secret") else {"value": fake.secret}
            if url.endswith("/start" + rollout.API):
                return {"name": "ibkrv2stg-bootstrap-abc1234"}
            raise AssertionError(url)

        state = {"container": rollout.validate_job(_job(), rollout.recorded_image()), "bundle": rollout.bundle(),
                 "head": "0" * 40}
        output = io.StringIO()
        with mock.patch.object(rollout, "request", fake), mock.patch.object(rollout, "preflight", return_value=state), \
                contextlib.redirect_stdout(output):
            self.assertEqual(rollout.main(["start"]), 0)
        password = rollout.collector_password(fake.secret)
        started = [data for method, url, data in calls if url.endswith("/start" + rollout.API)]
        self.assertEqual(len(started), 1)
        self.assertNotIn(password, json.dumps(started[0]))
        self.assertNotIn(password, output.getvalue())
        self.assertIn("ibkrv2stg-bootstrap-abc1234", output.getvalue())
        verifier = started[0]["containers"][0]["env"][1]["value"]
        self.assertRegex(verifier, payload.VERIFIER)

    def test_an_existing_secret_is_reused_not_replaced(self):
        def fake(method, url, *, data=None, audience=rollout.MANAGEMENT, missing=False):
            if method == "PUT" and url.startswith("https://" + rollout.VAULT):
                raise AssertionError("must not overwrite the secret")
            if url.startswith("https://" + rollout.VAULT):
                return {"value": rollout.collector_url(PASSWORD)}
            return {"name": "ibkrv2stg-bootstrap-abc1234"}

        state = {"container": rollout.validate_job(_job(), rollout.recorded_image()), "bundle": rollout.bundle(),
                 "head": "0" * 40}
        with mock.patch.object(rollout, "request", fake), mock.patch.object(rollout, "preflight", return_value=state), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rollout.main(["start"]), 0)


class JobValidationTests(unittest.TestCase):
    def test_accepts_the_expected_job(self):
        self.assertEqual(rollout.validate_job(_job(), rollout.recorded_image())["name"], "postgres-bootstrap")

    def test_accepts_a_different_identity_when_the_admin_binding_uses_it(self):
        other = IDENTITY.replace("ingestion", "bootstrap")
        job = _job(**{"identity.userAssignedIdentities": {other: {}}})
        job["properties"]["configuration"]["secrets"][0]["identity"] = other
        rollout.validate_job(job, rollout.recorded_image())

    def test_rejects_any_difference(self):
        other = IDENTITY.replace("ingestion", "web")
        cases = {
            "image": _job(**{"properties.template.containers.0.image": "registry.example/other@sha256:" + "0" * 64}),
            "two identities": _job(**{"identity.userAssignedIdentities": {IDENTITY: {}, other: {}}}),
            "binding identity": _job(**{"identity.userAssignedIdentities": {other: {}}}),
            "trigger": _job(**{"properties.configuration.triggerType": "Schedule"}),
            "retry": _job(**{"properties.configuration.replicaRetryLimit": 1}),
            "binding": _job(**{"properties.configuration.secrets": []}),
            "volumes": _job(**{"properties.template.volumes": [{"name": "x"}]}),
        }
        for name, job in cases.items():
            with self.subTest(name), self.assertRaises(SystemExit):
                rollout.validate_job(job, rollout.recorded_image())


class PayloadGuardTests(unittest.TestCase):
    def test_rejects_a_password_where_a_verifier_belongs(self):
        value = rollout.bundle()

        class Connection:
            class info:
                dbname = "ibkr_staging"

        with self.assertRaisesRegex(ValueError, "SCRAM"):
            payload.run(Connection(), value, PASSWORD)

    def test_rejects_migration_text_that_does_not_match_its_checksum(self):
        value = dict(rollout.bundle(), target_sql="SELECT 1;")
        with self.assertRaisesRegex(ValueError, "checksum"):
            payload.run(object(), value, rollout.scram_verifier(PASSWORD))


if __name__ == "__main__":
    unittest.main()
