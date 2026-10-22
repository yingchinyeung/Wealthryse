"""v2's alerts route to v2's own action group, so retiring v1.1 can't silence them."""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
V2_GROUP = "ibkrv2stg-alerts"
V11_GROUP = "ibkr-v11-daily-alerts"
# v1.1's own templates create or use v1.1's group; they go when v1.1 is deleted.
V11_TEMPLATES = {"job.bicep", "history-job.bicep", "bootstrap-job.bicep", "main.bicep"}
DATED = re.compile(r"_2026\d{4}\.py$")


class AlertActionGroupTest(unittest.TestCase):
    def test_the_v2_group_has_one_owner_email_from_a_parameter(self):
        text = (ROOT / "infra" / "platform-alert-action-group.bicep").read_text(encoding="utf-8")
        self.assertIn(f"param actionGroupName string = '{V2_GROUP}'", text)
        self.assertIn("param alertEmail string\n", text)
        self.assertEqual(text.count("emailAddress:"), 1)
        self.assertIn("emailAddress: alertEmail", text)
        self.assertIsNone(re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text), "no literal email address")

    def test_every_v2_alert_module_defaults_to_the_v2_group(self):
        modules = []
        for path in sorted((ROOT / "infra").glob("*.bicep")):
            if path.name in V11_TEMPLATES:
                continue
            text = path.read_text(encoding="utf-8")
            match = re.search(r"param actionGroupName string = '([^']+)'", text)
            if match:
                modules.append(path.name)
                self.assertEqual(match.group(1), V2_GROUP, path.name)
        self.assertGreaterEqual(len(modules), 7)

    def test_no_current_code_routes_to_the_v11_group(self):
        found = []
        for folder in ("src", "scripts"):
            for path in (ROOT / folder).rglob("*.py"):
                if not DATED.search(path.name) and V11_GROUP in path.read_text(encoding="utf-8"):
                    found.append(str(path.relative_to(ROOT)))
        self.assertEqual(found, [])


if __name__ == "__main__":
    unittest.main()
