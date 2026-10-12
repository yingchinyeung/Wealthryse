import copy
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_codex_identity_trial import CLIENT, PRINCIPAL, IMAGE, trial_template, source_code


class CodexIdentityTrialTests(unittest.TestCase):
    def setUp(self):
        self.job = {"identity": {"type": "UserAssigned", "userAssignedIdentities": {
            "identity": {"clientId": CLIENT, "principalId": PRINCIPAL}}},
            "properties": {"configuration": {"triggerType": "Manual", "replicaRetryLimit": 0,
                "manualTriggerConfig": {"parallelism": 1, "replicaCompletionCount": 1}},
                "template": {"containers": [{"name": "test", "image": IMAGE,
                    "resources": {"cpu": 1, "memory": "2Gi"},
                    "env": [{"name": "DATABASE_URL", "secretRef": "database"}],
                    "command": ["normal-production-command"], "args": ["do-not-run"]}]}}}

    def test_override_drops_secrets_and_original_work_and_preserves_job(self):
        before = copy.deepcopy(self.job)
        result = trial_template(self.job, "print('synthetic')")
        self.assertEqual(self.job, before)
        container = result["containers"][0]
        self.assertEqual(container["env"], [{"name": "AZURE_CLIENT_ID", "value": CLIENT}])
        self.assertEqual(container["command"], ["python"])
        self.assertNotIn("normal-production-command", str(result))
        self.assertNotIn("DATABASE_URL", str(result))
        self.assertNotIn("secretRef", str(result))

    def test_rejects_changed_identity_image_retry_schedule_and_mounts(self):
        for change in (
            lambda j: j["identity"].update(type="SystemAssigned"),
            lambda j: j["properties"]["configuration"].update(triggerType="Schedule"),
            lambda j: j["properties"]["configuration"].update(replicaRetryLimit=1),
            lambda j: j["properties"]["configuration"]["manualTriggerConfig"].update(parallelism=2),
            lambda j: j["properties"]["template"].update(volumes=[{"name": "source"}]),
            lambda j: j["properties"]["template"]["containers"][0].update(image="mutable:latest"),
            lambda j: j["properties"]["template"]["containers"][0].update(volumeMounts=[{"mountPath": "/source"}]),
        ):
            job = copy.deepcopy(self.job)
            change(job)
            with self.assertRaises(ValueError):
                trial_template(job, "print('synthetic')")

    def test_injected_source_compiles_without_executing_and_has_no_database_client(self):
        source = source_code()
        compile(source, "trial", "exec")
        self.assertNotIn("DATABASE_URL", source)
        self.assertNotIn("psycopg", source)
        self.assertIn("ManagedIdentityCredential", source)


if __name__ == "__main__":
    unittest.main()
