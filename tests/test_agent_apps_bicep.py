import re
import unittest
from pathlib import Path

BICEP = Path(__file__).resolve().parents[1] / "infra" / "agent-apps.bicep"


class AgentAppsBicepTest(unittest.TestCase):
    def test_job_names_fit_container_apps_limit(self):
        names = re.findall(r"\{\s*name:\s*'([^']+)',\s*job:", BICEP.read_text())
        self.assertEqual(len(names), 3)
        for name in names:
            self.assertTrue(2 <= len(name) <= 32, f"{name} is {len(name)} characters")

    def test_apps_stay_awake(self):
        replicas = re.findall(r"minReplicas:\s*(\d+)", BICEP.read_text())
        self.assertEqual(replicas, ["1", "1"])


if __name__ == "__main__":
    unittest.main()
