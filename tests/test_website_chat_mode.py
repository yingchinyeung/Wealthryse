import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("website_release", ROOT / "deploy" / "website_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)


class WebsiteChatModeTest(unittest.TestCase):
    def run_chat(self, mode, previous="owner"):
        app = {"properties": {"template": {"containers": [{"env": [{"name": "AGENT_UI_MODE", "value": previous}]}]},
                              "configuration": {"ingress": {"fqdn": "web.example"}}}}
        calls = []

        def az(*args, parse=True):
            calls.append(args)
            return app if args[1] == "show" else ""

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(release, "az", az), \
                mock.patch.object(release, "STATE", Path(folder) / "release-state.json"), \
                mock.patch.object(sys, "argv", ["website_release.py", "chat", mode]):
            release.chat()
            state = json.loads(release.STATE.read_text())
        update = calls[-1]
        return dict(a.split("=", 1) for a in update[update.index("--set-env-vars") + 1:]), state

    def test_all_users_gets_the_agent_address(self):
        settings, state = self.run_chat("all")
        self.assertEqual(settings["AGENT_UI_MODE"], "all")
        self.assertEqual(settings["AGENT_API_URL"], release.AGENT_SETTINGS["AGENT_API_URL"])
        self.assertEqual(state["previous_mode"], "owner")

    def test_off_only_switches_the_mode(self):
        settings, _ = self.run_chat("off")
        self.assertEqual(settings, {"AGENT_UI_MODE": "off"})

    def test_unknown_mode_is_refused(self):
        with mock.patch.object(sys, "argv", ["website_release.py", "chat", "everyone"]):
            with self.assertRaises(SystemExit):
                release.chat()


if __name__ == "__main__":
    unittest.main()
