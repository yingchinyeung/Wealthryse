"""The website release's first step: which changed files it ships, skips or refuses."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("website_release_prepare", ROOT / "deploy" / "website_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

HEAD, LIVE = "h" * 40, "d" * 40
APP = {"properties": {"template": {"containers": [{"image": release.REG + "/ibkr-platform:web-" + LIVE, "env": []}]},
                      "configuration": {"ingress": {"fqdn": "web.example"}, "secrets": []}}}


def prepare(changed):
    """Run prepare with the live website on LIVE and these files changed since then."""
    def git(*args, binary=False):
        if args[0] == "rev-parse":
            return HEAD + "\n"
        if args[0] == "diff":
            return "\n".join(changed) + "\n"
        raise AssertionError(args)

    with tempfile.TemporaryDirectory() as folder:
        state = Path(folder) / "release-state.json"
        with mock.patch.object(release, "STATE", state), \
                mock.patch.object(release, "git", side_effect=git), \
                mock.patch.object(release, "az", return_value=APP), \
                mock.patch.object(release, "secret_kinds", return_value={}), \
                mock.patch.object(release.subprocess, "run", return_value=mock.Mock(returncode=0)):
            release.prepare()
        return json.loads(state.read_text())


class PrepareTest(unittest.TestCase):
    def test_operator_powershell_scripts_are_not_part_of_the_website(self):
        state = prepare(["scripts/deploy_azure.ps1", "scripts/run_kiwoom_conform.py", "src/ibkr_etl/web.py"])
        self.assertEqual(state["changed_files"], ["src/ibkr_etl/web.py"])

    def test_other_image_files_still_stop_the_release(self):
        with self.assertRaises(SystemExit) as stopped:
            prepare(["Dockerfile.azure.extra", "src/ibkr_etl/web.py"])
        self.assertIn("only ships src/ibkr_etl", str(stopped.exception))


if __name__ == "__main__":
    unittest.main()
