"""The ACR Tasks website build: the same swap as the build VM, refused when live files drifted."""
import hashlib
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load("website_release_acr", ROOT / "deploy" / "website_release.py")
apply_script = load("website_acr_apply", ROOT / "deploy" / "website_acr_apply.py")

BEFORE = {"web.py": b"from .db import connect\n", "db.py": b"db\n", "old.py": b"old\n",
          "report_design_ui.py": b"SCRIPT = ''\n"}
AFTER = {"web.py": b"from .db import connect\nfrom . import report_design_ui\nfrom . import fresh\n", "db.py": b"db\n",
         "fresh.py": b"fresh\n", "report_design_ui.py": b"SCRIPT = ''\n"}
STATE = {"head": "h" * 40, "deployed_commit": "d" * 40, "changed_files": ["src/ibkr_etl/web.py"],
         "live_image": release.REG + "/ibkr-platform:web-" + "d" * 40, "target_image": release.REG + "/ibkr-platform:web-" + "h" * 40}


class AcrBuildTest(unittest.TestCase):
    def context(self, folder):
        with mock.patch.object(release, "sides", return_value=(dict(BEFORE), dict(AFTER))):
            return release.acr_context(STATE, folder)

    def live_tree(self, folder, files):
        src = Path(folder) / "src"
        src.mkdir()
        for name, data in files.items():
            (src / name).write_bytes(data)
        return src

    def run_apply(self, live):
        with tempfile.TemporaryDirectory() as tmp:
            built = self.context(Path(tmp))
            built["runtime_user"] = f"{os.getuid()}:{os.getgid()}"
            src = self.live_tree(tmp, live)
            apply_script.apply(built, src=src, files=Path(tmp) / "release" / "files")
            return {p.name: p.read_bytes() for p in src.iterdir()}

    def test_dockerfile_starts_from_the_live_image_and_checks_as_the_runtime_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = self.context(Path(tmp))
            lines = (Path(tmp) / "Dockerfile").read_text().splitlines()
            self.assertEqual(lines[0], "FROM " + STATE["live_image"])
            self.assertEqual(sorted(built["touched"]), ["fresh.py", "old.py", "web.py"])
            self.assertEqual(built["imported"], {"report_design_ui.py": hashlib.sha256(AFTER["report_design_ui.py"]).hexdigest()})
            self.assertLess(lines.index("USER " + release.RUNTIME_USER), lines.index('RUN ["/usr/local/bin/python3", "-c", "import ibkr_etl.web"]'))
            self.assertIn("scripts/check_report_runtime.py", lines[-1])
            self.assertEqual(sorted(p.name for p in (Path(tmp) / "release" / "files").iterdir()),
                             ["fresh.py", "report_design_ui.py", "web.py"])
            self.assertIn("removed = ['old.py']", (Path(tmp) / "release" / "install.py").read_text())

    def test_reviewed_files_replace_the_live_ones(self):
        live = {"web.py": BEFORE["web.py"], "db.py": b"live-only db\n", "old.py": b"old\n"}
        self.assertEqual(self.run_apply(live), {"web.py": AFTER["web.py"], "db.py": b"live-only db\n",
                                                 "fresh.py": b"fresh\n", "report_design_ui.py": b"SCRIPT = ''\n"})

    def test_windows_line_endings_and_files_already_as_reviewed_are_accepted(self):
        live = {"web.py": BEFORE["web.py"].replace(b"\n", b"\r\n"), "fresh.py": b"fresh\n", "report_design_ui.py": b"stale\n"}
        result = self.run_apply(live)
        self.assertEqual(result["web.py"], AFTER["web.py"])
        self.assertEqual(result["report_design_ui.py"], AFTER["report_design_ui.py"])

    def test_a_drifted_live_file_stops_the_build(self):
        with self.assertRaises(SystemExit) as stop:
            self.run_apply({"web.py": b"live-only change\n"})
        self.assertIn("web.py", str(stop.exception))

    def test_another_runtime_user_stops_the_build(self):
        with tempfile.TemporaryDirectory() as tmp:
            built = self.context(Path(tmp))
            built["runtime_user"] = f"{os.getuid() + 1}:0"
            src = self.live_tree(tmp, {"web.py": BEFORE["web.py"]})
            with self.assertRaises(SystemExit):
                apply_script.apply(built, src=src, files=Path(tmp) / "release" / "files")

    def test_the_pushed_digest_comes_from_the_succeeded_run(self):
        runs = [{"status": "Failed", "outputImages": []},
                {"status": "Succeeded", "outputImages": [{"repository": "ibkr-platform", "tag": "web-" + "h" * 40, "digest": "sha256:" + "a" * 64}]}]
        with tempfile.TemporaryDirectory() as tmp:
            state_file = Path(tmp) / "release-state.json"
            state_file.write_text(json.dumps(STATE))
            with mock.patch.object(release, "STATE", state_file), \
                 mock.patch.object(release, "sides", return_value=(dict(BEFORE), dict(AFTER))), \
                 mock.patch.object(release.subprocess, "run", return_value=mock.Mock(returncode=0)) as run, \
                 mock.patch.object(release, "az", return_value=runs):
                release.acr_build()
            self.assertEqual(run.call_args[0][0][:6], ["az", "acr", "build", "-r", "ibkrv2stgauhecwc4itaoo", "-t"])
            self.assertEqual(json.loads(state_file.read_text())["pushed_image"], release.IMAGE_PREFIX + "a" * 64)


if __name__ == "__main__":
    unittest.main()
