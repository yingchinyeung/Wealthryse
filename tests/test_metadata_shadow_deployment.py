import base64
import importlib.util
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import deploy_metadata_monitor as deployment


class MetadataShadowDeploymentTests(unittest.TestCase):
    def test_monitor_never_receives_admin_identity_or_secret(self):
        resource = deployment.definition()
        self.assertEqual(resource["properties"]["configuration"]["triggerType"], "Manual")
        text = str(resource)
        # Inspect configuration only: payload includes permission-test names.
        config = str(resource["properties"]["configuration"])
        self.assertNotIn("database-admin-url", config)
        self.assertNotIn("ingestion-identity", text)
        self.assertNotIn("approval-url", config)
        self.assertEqual(len(resource["properties"]["configuration"]["secrets"]), 1)

    def test_schedule_is_shadow_and_zero_retry(self):
        resource = deployment.definition(scheduled=True)
        config = resource["properties"]["configuration"]
        self.assertEqual(config["replicaRetryLimit"], 0)
        self.assertEqual(config["scheduleTriggerConfig"]["cronExpression"], "7,37 * * * *")
        self.assertEqual(resource["tags"]["mode"], "shadow")
        self.assertIn("@sha256:", resource["properties"]["template"]["containers"][0]["image"])

    def test_provision_has_no_schedule(self):
        config = deployment.definition(provision=True)["properties"]["configuration"]
        self.assertEqual(config["triggerType"], "Manual")
        self.assertEqual(config["replicaRetryLimit"], 0)
        self.assertNotIn("scheduleTriggerConfig", config)

    def test_bundles_compile_and_include_correct_entrypoint(self):
        for provision in (False, True):
            args, digest = deployment.bundle(provision)
            encoded = args[1].split("b64decode('")[1].split("')")[0]
            source = base64.b64decode(encoded).decode()
            compile(source, "bundled-monitor", "exec")
            namespace = {"__name__":"test_bundle"}
            exec(compile(source, "bundled-monitor", "exec"), namespace)
            self.assertIn("verify_access", namespace)
            self.assertEqual(len(digest), 64)
            self.assertEqual("MONITOR_DATABASE_URL" in source, provision)

    def test_errors_are_sanitized(self):
        for name in ("provision_metadata_monitor.py", "run_metadata_shadow_monitor.py"):
            source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
            self.assertIn('"error_type":type(error).__name__', source)
            self.assertNotIn("str(error)", source)

    def test_failed_execution_cannot_enable_schedule(self):
        with patch.object(deployment, "request", return_value={"properties":{"status":"Failed"}}), patch.object(deployment, "az") as query:
            with self.assertRaisesRegex(RuntimeError, "not succeeded"):
                deployment.verify_execution(deployment.JOB, deployment.JOB + "-abcdefg", "METADATA_MONITOR")
            query.assert_not_called()

    def test_unreviewed_execution_payload_is_rejected(self):
        properties = deployment.definition()["properties"]
        properties["status"] = "Succeeded"
        properties["template"]["containers"][0]["image"] = "unreviewed:latest"
        with patch.object(deployment, "request", return_value={"properties":properties}):
            with self.assertRaisesRegex(RuntimeError, "differs"):
                deployment.verify_execution(deployment.JOB, deployment.JOB + "-abcdefg", "METADATA_MONITOR")

    def test_truncated_logs_cannot_enable_schedule(self):
        properties = deployment.definition()["properties"]
        properties["status"] = "Succeeded"
        logs = [{"Log_s":'METADATA_MONITOR {"kind":"complete","records":1,"access_check":"passed"}'}]
        with patch.object(deployment, "request", side_effect=[{"properties":properties}, deployment.definition()]), patch.object(deployment, "az", return_value=logs):
            with self.assertRaisesRegex(RuntimeError, "Missing monitor"):
                deployment.verify_execution(deployment.JOB, deployment.JOB + "-abcdefg", "METADATA_MONITOR")

    def test_azure_alias_and_resource_id_case_are_accepted(self):
        executed = deployment.definition()["properties"]
        executed["status"] = "Succeeded"
        executed["template"]["containers"][0]["env"][1]["secretRef"] = "cappjob-" + deployment.JOB
        current = deployment.definition()
        current["identity"]["userAssignedIdentities"] = {k.lower():v for k,v in current["identity"]["userAssignedIdentities"].items()}
        logs = [{"Log_s":'METADATA_MONITOR {"kind":"complete","records":0,"access_check":"passed"}'}]
        with patch.object(deployment, "request", side_effect=[{"properties":executed}, current]), patch.object(deployment, "az", return_value=logs), patch.object(Path, "mkdir"), patch.object(Path, "write_text"):
            deployment.verify_execution(deployment.JOB, deployment.JOB + "-abcdefg", "METADATA_MONITOR")

    def test_wrong_keyvault_binding_is_rejected(self):
        executed = deployment.definition()["properties"]
        executed["status"] = "Succeeded"
        current = deployment.definition()
        current["properties"]["configuration"]["secrets"][0]["keyVaultUrl"] = "https://wrong.vault.azure.net/secrets/admin"
        with patch.object(deployment, "request", side_effect=[{"properties":executed}, current]):
            with self.assertRaisesRegex(RuntimeError, "Key Vault binding"):
                deployment.verify_execution(deployment.JOB, deployment.JOB + "-abcdefg", "METADATA_MONITOR")


if __name__ == "__main__":
    unittest.main()
