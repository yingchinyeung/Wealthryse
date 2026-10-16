import importlib.util
import os
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("crisis_job", ROOT / "scripts" / "refresh_crisis_scenarios.py")
job = importlib.util.module_from_spec(spec)
spec.loader.exec_module(job)


class CrisisScenariosJobTests(unittest.TestCase):
    def run_job(self, summary, enabled="true"):
        refresh = mock.Mock(return_value=summary)
        with mock.patch.dict(os.environ, {"CRISIS_SCENARIOS_ENABLED": enabled}), \
                mock.patch.object(job, "_crisis_scenario_subjects", return_value=(["U0000001"], ["demo-composite"])), \
                mock.patch.object(job.crisis_scenarios, "refresh", refresh):
            return job.run(object()), refresh

    def test_switched_off_does_nothing_and_passes(self):
        (code, result), refresh = self.run_job({}, enabled="false")
        self.assertEqual((code, result["status"]), (0, "disabled"))
        refresh.assert_not_called()

    def test_every_subject_stored_passes(self):
        (code, result), refresh = self.run_job({"version": "v", "subjects": {"U0000001": {}, "demo-composite": {}},
                                                "failures": {}})
        self.assertEqual((code, result["status"]), (0, "success"))
        self.assertEqual(refresh.call_args.kwargs["accounts"], ["U0000001"])
        self.assertEqual(refresh.call_args.kwargs["composites"], ["demo-composite"])

    def test_a_failed_subject_fails_the_job(self):
        (code, result), _ = self.run_job({"version": "v", "subjects": {"U0000001": {}},
                                          "failures": {"demo-composite": "no prices"}})
        self.assertEqual((code, result["status"]), (1, "failed"))

    def test_nothing_stored_fails_the_job(self):
        (code, _), _ = self.run_job({"version": "v", "subjects": {}, "failures": {}})
        self.assertEqual(code, 1)

    def test_job_runs_after_the_daily_cycle_without_retries(self):
        template = (ROOT / "infra" / "staging-crisis-scenarios-job.bicep").read_text(encoding="utf-8")
        self.assertIn("cronExpression: '20 9 * * *'", template)
        self.assertIn("replicaRetryLimit: 0", template)
        self.assertIn("args: ['scripts/refresh_crisis_scenarios.py']", template)
        self.assertIn("{ name: 'CRISIS_SCENARIOS_ENABLED', value: 'true' }", template)
        self.assertIn("ibkr-platform@${imageDigest}", template)
        self.assertIn("secrets/database-app-url", template)


if __name__ == "__main__":
    unittest.main()
