"""The website release ships src/ibkr_etl from the repo; the old live overlays are gone."""
import hashlib
import importlib.util
import subprocess
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


release = load("website_release_sources", ROOT / "deploy" / "website_release.py")
remote = load("website_overlay_build_remote_sources", ROOT / "deploy" / "website_overlay_build_remote.py")

DEPLOYED, HEAD = "d" * 40, "h" * 40
TREES = {
    DEPLOYED: {
        "src/ibkr_etl/web.py": b"repo web before\n",
        "src/ibkr_etl/db.py": b"db\n",
        "deploy/live-merged/src/ibkr_etl/web.py": b"live web\n",
    },
    HEAD: {
        "src/ibkr_etl/web.py": b"live web\nnew line\n",
        "src/ibkr_etl/db.py": b"db\n",
        "src/ibkr_etl/risk_display.py": b"risk\n",
    },
}


def fake_git(*args, binary=False):
    if args[0] == "ls-tree":
        commit, prefix = args[3], args[4]
        return "\n".join(p for p in TREES[commit] if p.startswith(prefix))
    if args[0] == "show":
        commit, path = args[1].split(":", 1)
        return TREES[commit][path]
    raise AssertionError(args)


def sha(data):
    return hashlib.sha256(data).hexdigest()


class ChangeSetTest(unittest.TestCase):
    def test_live_merged_copies_at_the_deployed_commit_are_the_before_side(self):
        with mock.patch.object(release, "git", side_effect=fake_git):
            patch, before, after = release.change_set({"deployed_commit": DEPLOYED, "head": HEAD})
        self.assertEqual(before["web.py"], sha(b"live web\n"))
        self.assertEqual(after["web.py"], sha(b"live web\nnew line\n"))
        self.assertNotIn("risk_display.py", before)
        self.assertIn(b"+new line", patch)
        self.assertNotIn(b"repo web before", patch)
        self.assertNotIn(b"db.py", patch)

    def test_live_image_not_built_from_the_repo_is_refused(self):
        with mock.patch.object(release, "git", side_effect=fake_git):
            with self.assertRaises(SystemExit):
                release.change_set({"deployed_commit": release.BASELINE_COMMIT, "head": HEAD})

    def test_overlay_folders_are_gone(self):
        self.assertFalse((ROOT / "deploy" / "live-snapshot").exists())
        self.assertFalse((ROOT / "deploy" / "live-merged").exists())
        self.assertNotIn("live-snapshot", (ROOT / "deploy" / "website_release.py").read_text())


class FirstImportedTest(unittest.TestCase):
    BEFORE = {
        "web.py": b"from .db import connect\n",
        "db.py": b"import json\n",
        "report_design_ui.py": b"SCRIPT = ''\n",
        "unused.py": b"x = 1\n",
    }

    def test_unchanged_modules_the_new_website_imports_first_are_added(self):
        after = {**self.BEFORE,
                 "web.py": b"from .db import connect\nfrom . import report_design_ui\nfrom ibkr_etl.fresh import y\n",
                 "fresh.py": b"def y(): from .unused import x\n"}
        self.assertEqual(release.website_modules(after), {"web.py", "db.py", "report_design_ui.py", "fresh.py", "unused.py"})
        # fresh.py is new, so the patch carries it; the other two did not change.
        self.assertEqual(release.first_imported(self.BEFORE, after), ["report_design_ui.py", "unused.py"])

    def test_nothing_is_added_when_the_website_imports_nothing_new(self):
        after = {**self.BEFORE, "web.py": b"from .db import connect\nVALUE = 2\n"}
        self.assertEqual(release.first_imported(self.BEFORE, after), [])

    def test_the_build_adds_them_whole_and_checks_each_against_the_reviewed_hash(self):
        code = (ROOT / "deploy" / "website_overlay_build_remote.py").read_text()
        self.assertIn("hashlib.sha256(content).hexdigest() != wanted.get(name)", code)
        self.assertLess(code.index("The change altered files it should not have"), code.index("result['first_imported']"))
        self.assertLess(code.index("result['first_imported']"), code.index("'import ibkr_etl.web'"))


class AlreadyLiveFilesTest(unittest.TestCase):
    def test_sections_for_files_already_live_are_dropped(self):
        patch = (b"diff --git a/src/ibkr_etl/web.py b/src/ibkr_etl/web.py\n--- a/src/ibkr_etl/web.py\n"
                 b"+++ b/src/ibkr_etl/web.py\n@@ -1 +1 @@\n-x\n+y\n"
                 b"diff --git b/src/ibkr_etl/risk_display.py b/src/ibkr_etl/risk_display.py\nnew file mode 100644\n"
                 b"--- /dev/null\n+++ b/src/ibkr_etl/risk_display.py\n@@ -0,0 +1 @@\n+risk\n")
        kept = remote.without_files(patch, ["risk_display.py"])
        self.assertIn(b"src/ibkr_etl/web.py", kept)
        self.assertNotIn(b"risk_display", kept)
        self.assertEqual(remote.without_files(patch, []), patch)
        self.assertEqual(remote.without_files(patch, ["web.py", "risk_display.py"]), b"")

    def test_the_build_skips_files_already_as_reviewed(self):
        code = (ROOT / "deploy" / "website_overlay_build_remote.py").read_text()
        self.assertIn("already = [n for n in touched if n in live and live[n] == wanted.get(n)]", code)
        self.assertIn("without_files(gzip.decompress(base64.b64decode(config['patch'])), already)", code)

    def test_a_trimmed_patch_applies_with_gnu_patch(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            pkg = Path(tmp) / "src" / "ibkr_etl"
            pkg.mkdir(parents=True)
            (pkg / "web.py").write_text("x\n")
            (pkg / "risk_display.py").write_text("risk\n")
            patch = (b"diff --git a/src/ibkr_etl/web.py b/src/ibkr_etl/web.py\n--- a/src/ibkr_etl/web.py\n"
                     b"+++ b/src/ibkr_etl/web.py\n@@ -1 +1 @@\n-x\n+y\n"
                     b"diff --git b/src/ibkr_etl/risk_display.py b/src/ibkr_etl/risk_display.py\nnew file mode 100644\n"
                     b"--- /dev/null\n+++ b/src/ibkr_etl/risk_display.py\n@@ -0,0 +1 @@\n+risk\n")
            (Path(tmp) / "change.patch").write_bytes(remote.without_files(patch, ["risk_display.py"]))
            applied = subprocess.run(["patch", "-p1", "--forward", "--fuzz=0", "--no-backup-if-mismatch", "-i", "change.patch"],
                                     cwd=tmp, capture_output=True, text=True)
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertEqual((pkg / "web.py").read_text(), "y\n")
            self.assertEqual((pkg / "risk_display.py").read_text(), "risk\n")


class PrepareTest(unittest.TestCase):
    def run_prepare(self, changed, env=()):
        app = {"properties": {"template": {"containers": [{"image": "reg/ibkr-platform:web-" + DEPLOYED, "env": list(env)}]},
                              "configuration": {"ingress": {"fqdn": "site.example"}}}}

        def git(*args, binary=False):
            if args[0] == "rev-parse":
                return HEAD + "\n"
            if args[0] == "diff":
                return "\n".join(changed)
            raise AssertionError(args)

        import json, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "release-state.json"
            with mock.patch.object(release, "git", side_effect=git), \
                    mock.patch.object(release, "az", return_value=app), \
                    mock.patch.object(release, "STATE", state), \
                    mock.patch.object(release.subprocess, "run", return_value=mock.Mock(returncode=0)), \
                    mock.patch("builtins.print"):
                release.prepare()
            return json.loads(state.read_text())

    def test_postgres_migrations_are_not_shipped_and_do_not_stop_the_release(self):
        state = self.run_prepare(["migrations/postgres/064_onboarding.sql", "src/ibkr_etl/web.py"])
        self.assertEqual(state["changed_files"], ["src/ibkr_etl/web.py"])
        self.assertEqual(state["migrations_not_shipped"], ["migrations/postgres/064_onboarding.sql"])

    def test_the_license_file_does_not_stop_the_release(self):
        state = self.run_prepare(["LICENSE", "src/ibkr_etl/web.py"])
        self.assertEqual(state["changed_files"], ["src/ibkr_etl/web.py"])

    def test_other_image_files_still_stop_the_release(self):
        with self.assertRaises(SystemExit):
            self.run_prepare(["Dockerfile", "src/ibkr_etl/web.py"])

    def test_onboarding_switched_on_stops_the_release(self):
        with self.assertRaises(SystemExit) as stopped:
            self.run_prepare(["src/ibkr_etl/web.py"], env=[{"name": "ONBOARDING_V2_ENABLED", "value": "True"}])
        self.assertIn("064_broker_connections_and_onboarding_cases.sql", str(stopped.exception))

    def test_onboarding_switch_absent_or_off_does_not_stop_the_release(self):
        for env in ([], [{"name": "ONBOARDING_V2_ENABLED", "value": "false"}], [{"name": "OTHER", "secretRef": "x"}]):
            self.assertEqual(self.run_prepare(["src/ibkr_etl/web.py"], env=env)["changed_files"], ["src/ibkr_etl/web.py"])


if __name__ == "__main__":
    unittest.main()
