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


class SubpackageTest(unittest.TestCase):
    """The factsheet designer (14 Oct) added src/ibkr_etl/factsheet_spec/; run 38984176348 failed on it."""
    AFTER = {
        "web.py": b"from . import factsheet_design_web\n",
        "factsheet_design_web.py": b"from .factsheet_spec import builder\nfrom .factsheet_spec.engine import render\n",
        "factsheet_spec/__init__.py": b"",
        "factsheet_spec/builder.py": b"from . import draft\nfrom .spec import x\nfrom .. import metrics_guide\n",
        "factsheet_spec/engine.py": b"from .spec import x\n",
        "factsheet_spec/draft.py": b"",
        "factsheet_spec/spec.py": b"",
        "factsheet_spec/stress.py": b"",
        "metrics_guide.py": b"",
        "other_job.py": b"",
    }

    def test_subpackage_files_the_website_imports_are_website_modules(self):
        self.assertEqual(release.website_modules(self.AFTER), set(self.AFTER) - {"factsheet_spec/stress.py", "other_job.py"})

    def test_the_change_carries_new_subpackage_files(self):
        before = {"web.py": b"", "metrics_guide.py": b"", "other_job.py": b""}
        with mock.patch.object(release, "sides", return_value=(before, dict(self.AFTER))):
            patch, live, new = release.change_set({})
        self.assertIn(b"src/ibkr_etl/factsheet_spec/engine.py", patch)
        self.assertIn("factsheet_spec/engine.py", new)


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
        self.assertIn("without_files(gzip.decompress(base64.b64decode(config['patch'])), already + skipped)", code)

    def test_files_the_website_does_not_import_and_never_had_are_left_out(self):
        code = (ROOT / "deploy" / "website_overlay_build_remote.py").read_text()
        self.assertIn("skipped = [n for n in touched if modules is not None and n not in modules and n not in live]", code)
        self.assertLess(code.index("touched = [n for n in touched if n not in skipped]"),
                        code.index("is not where the change expects it"))
        self.assertIn("'website_modules': sorted(website_modules(after))",
                      (ROOT / "deploy" / "website_release.py").read_text())

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



class DesignsTest(unittest.TestCase):
    """The designer reads factsheet_spec/designs/*.json (built-in designs); the website gets them whole."""
    KOREA = "factsheet_spec/designs/korea-istrategy.json"

    def test_design_files_are_the_json_files_directly_in_the_designs_folder(self):
        trees = {HEAD: {"src/ibkr_etl/factsheet_spec/designs/korea-istrategy.json": b"{}",
                        "src/ibkr_etl/factsheet_spec/designs/notes.txt": b"x",
                        "src/ibkr_etl/factsheet_spec/designs/old/a.json": b"{}",
                        "src/ibkr_etl/factsheet_spec/spec.py": b""}}

        def git(*args, binary=False):
            if args[0] == "ls-tree":
                return "\n".join(p for p in trees[args[3]] if p.startswith(args[4]))
            commit, path = args[1].split(":", 1)
            return trees[commit][path]

        with mock.patch.object(release, "git", side_effect=git):
            self.assertEqual(release.design_files(HEAD), {self.KOREA: b"{}"})

    def test_the_build_sends_every_design_with_its_hash(self):
        sent = {}

        def capture(config):
            sent.update(config)
            raise RuntimeError("stop before Azure")

        with mock.patch.object(release, "change_set", return_value=(b"", {}, {})), \
                mock.patch.object(release, "sides", return_value=({}, {})), \
                mock.patch.object(release, "design_files", return_value={self.KOREA: b"{}"}), \
                mock.patch.object(release, "remote_script", side_effect=capture), \
                self.assertRaises(RuntimeError):
            release.overlay_build({"head": HEAD, "live_image": "live", "target_image": "target"})
        self.assertEqual(sent["design_hashes"], {self.KOREA: sha(b"{}")})
        self.assertEqual(remote.gzip.decompress(remote.base64.b64decode(sent["designs"][self.KOREA])), b"{}")

    def test_the_build_writes_them_after_the_patch_checks_and_the_image_loads_them(self):
        code = (ROOT / "deploy" / "website_overlay_build_remote.py").read_text()
        self.assertIn("!= design_hashes.get(name)", code)
        self.assertLess(code.index("The change altered files it should not have"), code.index("result['designs_written']"))
        self.assertLess(code.index("result['designs_written']"), code.index("'import ibkr_etl.web'"))
        self.assertIn("DESIGN_CHECK.replace('IDS', repr(ids))", code)

    def test_the_image_check_passes_for_the_repo_designs_and_fails_for_a_missing_one(self):
        ids = sorted(p.stem for p in (ROOT / "src" / "ibkr_etl" / "factsheet_spec" / "designs").glob("*.json"))
        self.assertIn("korea-istrategy", ids)
        exec(remote.DESIGN_CHECK.replace("IDS", repr(ids)), {})
        with self.assertRaises(AssertionError):
            exec(remote.DESIGN_CHECK.replace("IDS", repr(ids + ["missing-design"])), {})


class PrepareTest(unittest.TestCase):
    def run_prepare(self, changed, env=(), applied=None, migrations=None, pyproject=None, files=None):
        app = {"properties": {"template": {"containers": [{"image": "reg/ibkr-platform:web-" + DEPLOYED, "env": list(env)}]},
                              "configuration": {"ingress": {"fqdn": "site.example"}}}}

        def git(*args, binary=False):
            if args[0] == "rev-parse":
                return HEAD + "\n"
            if args[0] == "diff":
                return "\n".join(changed)
            if args[0] == "show" and args[1].endswith(":pyproject.toml"):
                return pyproject[args[1].split(":")[0]]
            if args[0] == "show" and args[1].startswith(HEAD + ":migrations/postgres/"):
                return (migrations or {})[args[1].rsplit("/", 1)[1]]
            if args[0] == "show" and args[1] in (files or {}):
                return files[args[1]]
            raise AssertionError(args)

        import json, tempfile
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "release-state.json"
            record = Path(tmp) / "applied-migrations.json"
            record.write_text(json.dumps({"migrations": applied or {}}))
            with mock.patch.object(release, "git", side_effect=git), \
                    mock.patch.object(release, "APPLIED_MIGRATIONS", record), \
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

    def test_a_built_in_design_change_is_shipped(self):
        state = self.run_prepare(["src/ibkr_etl/factsheet_spec/designs/korea-istrategy.json", "src/ibkr_etl/web.py"])
        self.assertEqual(state["changed_files"], ["src/ibkr_etl/factsheet_spec/designs/korea-istrategy.json", "src/ibkr_etl/web.py"])

    def test_the_license_file_does_not_stop_the_release(self):
        state = self.run_prepare(["LICENSE", "src/ibkr_etl/web.py"])
        self.assertEqual(state["changed_files"], ["src/ibkr_etl/web.py"])

    PYPROJECT = ('[project]\nname = "ibkr-etl"\ndependencies = ["psycopg>=3"]\n\n'
                 '[tool.setuptools.package-data]\nibkr_etl = ["assets/a.pdf"]\n')

    def test_a_pyproject_change_to_package_data_only_does_not_stop_the_release(self):
        head = self.PYPROJECT.replace('["assets/a.pdf"]', '["assets/a.pdf", "metrics_guide_sources.json"]')
        state = self.run_prepare(["pyproject.toml", "src/ibkr_etl/web.py"],
                                 pyproject={DEPLOYED: self.PYPROJECT, HEAD: head})
        self.assertEqual(state["changed_files"], ["src/ibkr_etl/web.py"])

    def test_a_pyproject_dependency_change_still_stops_the_release(self):
        head = self.PYPROJECT.replace('"psycopg>=3"', '"psycopg>=3", "httpx"')
        with self.assertRaises(SystemExit):
            self.run_prepare(["pyproject.toml", "src/ibkr_etl/web.py"], pyproject={DEPLOYED: self.PYPROJECT, HEAD: head})

    def test_other_image_files_still_stop_the_release(self):
        with self.assertRaises(SystemExit):
            self.run_prepare(["Dockerfile", "src/ibkr_etl/web.py"])

    DOCKERFILE = "FROM python\nCOPY src ./src\nCOPY queries ./queries\n\nRUN pip install .\n"
    MEDALLION = ("# Medallion data contracts and registers (infra/medallion), read by the layer quality checks\n"
                 "# and the certified figures. The rest of infra/ stays out of the image.\n"
                 "COPY infra/medallion ./medallion\nENV MEDALLION_DIR=/app/medallion\n")

    def medallion_files(self, dockerfile_head=None):
        return {DEPLOYED + ":Dockerfile.azure": self.DOCKERFILE,
                HEAD + ":Dockerfile.azure": dockerfile_head or self.DOCKERFILE.replace("\n\nRUN", "\n" + self.MEDALLION + "\nRUN"),
                DEPLOYED + ":.dockerignore": "tests\ninfra\n*.md\n",
                HEAD + ":.dockerignore": "tests\ninfra\n!infra/medallion\n*.md\n"}

    def test_the_lines_adding_infra_medallion_to_the_platform_image_do_not_stop_the_release(self):
        state = self.run_prepare([".dockerignore", "Dockerfile.azure", "src/ibkr_etl/web.py"], files=self.medallion_files())
        self.assertEqual(state["changed_files"], ["src/ibkr_etl/web.py"])

    def test_any_other_change_to_those_files_still_stops_the_release(self):
        head = self.DOCKERFILE.replace("\n\nRUN", "\n" + self.MEDALLION + "COPY infra ./infra\n\nRUN")
        with self.assertRaises(SystemExit):
            self.run_prepare(["Dockerfile.azure", "src/ibkr_etl/web.py"], files=self.medallion_files(head))

    ON = [{"name": "ONBOARDING_V2_ENABLED", "value": "True"}]
    SQL = {"064_broker_connections_and_onboarding_cases.sql": b"create 064\n",
           "065_onboarding_report_style.sql": b"alter 065\n"}

    def recorded(self, sql):
        import hashlib
        return {name: {"sha256": hashlib.sha256(body).hexdigest()} for name, body in sql.items()}

    def test_onboarding_switched_on_without_its_migrations_recorded_stops_the_release(self):
        with self.assertRaises(SystemExit) as stopped:
            self.run_prepare(["src/ibkr_etl/web.py"], env=self.ON, migrations=self.SQL)
        self.assertIn("064_broker_connections_and_onboarding_cases.sql", str(stopped.exception))
        self.assertIn("065_onboarding_report_style.sql", str(stopped.exception))

    def test_onboarding_switched_on_with_its_migrations_recorded_is_released(self):
        state = self.run_prepare(["src/ibkr_etl/web.py"], env=self.ON, applied=self.recorded(self.SQL), migrations=self.SQL)
        self.assertEqual(state["changed_files"], ["src/ibkr_etl/web.py"])

    def test_a_recorded_migration_that_differs_from_main_still_stops_the_release(self):
        changed = dict(self.SQL, **{"065_onboarding_report_style.sql": b"alter 065 differently\n"})
        with self.assertRaises(SystemExit) as stopped:
            self.run_prepare(["src/ibkr_etl/web.py"], env=self.ON, applied=self.recorded(self.SQL), migrations=changed)
        self.assertIn("065_onboarding_report_style.sql", str(stopped.exception))
        self.assertNotIn("064_broker", str(stopped.exception))

    def test_the_repository_records_the_onboarding_migrations_as_on_main(self):
        import hashlib, json
        recorded = json.loads(release.APPLIED_MIGRATIONS.read_text())["migrations"]
        # Only migrations confirmed applied are recorded; the crisis scenarios one is
        # added after the bootstrap job has run it.
        for switch in ("ONBOARDING_V2_ENABLED",):
            for name in release.SWITCHES_NEEDING_MIGRATIONS[switch]:
                body = (ROOT / "migrations" / "postgres" / name).read_bytes()
                self.assertEqual(recorded[name]["sha256"], hashlib.sha256(body).hexdigest(), name)

    def test_crisis_scenarios_switched_on_before_its_migration_is_recorded_stops_the_release(self):
        env = [{"name": "CRISIS_SCENARIOS_ENABLED", "value": "true"}]
        with self.assertRaises(SystemExit) as stopped:
            self.run_prepare(["src/ibkr_etl/web.py"], env=env, migrations={"066_crisis_scenarios.sql": b"create 066\n"})
        self.assertIn("066_crisis_scenarios.sql", str(stopped.exception))

    def test_onboarding_switch_absent_or_off_does_not_stop_the_release(self):
        for env in ([], [{"name": "ONBOARDING_V2_ENABLED", "value": "false"}], [{"name": "OTHER", "secretRef": "x"}]):
            self.assertEqual(self.run_prepare(["src/ibkr_etl/web.py"], env=env)["changed_files"], ["src/ibkr_etl/web.py"])

    def test_quant_and_desk_pages_need_their_held_migrations(self):
        self.assertTrue(release.switch_is_on("QUANT_ANALYTICS_PAGES", "performance=shadow"))
        self.assertTrue(release.switch_is_on("QUANT_ANALYTICS_PAGES", "performance=off, risk=On"))
        for value in ("", "off", "performance=off,risk=off", "performance"):
            self.assertFalse(release.switch_is_on("QUANT_ANALYTICS_PAGES", value), value)
        self.assertTrue(release.switch_is_on("DESK_PAGE", "owners"))
        self.assertTrue(release.switch_is_on("DESK_PAGE", " ON "))
        for value in ("", "off", "true"):
            self.assertFalse(release.switch_is_on("DESK_PAGE", value), value)
        for name, value, migration in (("QUANT_ANALYTICS_PAGES", "risk=shadow", "076_quant_analytics.sql"),
                                       ("DESK_PAGE", "owners", "077_desk_live_views.sql")):
            with self.subTest(switch=name), self.assertRaises(SystemExit) as stopped:
                self.run_prepare(["src/ibkr_etl/web.py"], env=[{"name": name, "value": value}],
                                 migrations={migration: b"create\n"})
            self.assertIn(migration, str(stopped.exception))
        for env in ([{"name": "QUANT_ANALYTICS_PAGES", "value": "performance=off"}], [{"name": "DESK_PAGE", "value": "off"}]):
            self.assertEqual(self.run_prepare(["src/ibkr_etl/web.py"], env=env)["changed_files"], ["src/ibkr_etl/web.py"])

    def test_the_research_image_files_are_not_website_changes(self):
        for path in ("Dockerfile.research", "requirements-research.lock"):
            self.assertTrue(path.startswith(release.IGNORED_PREFIXES), path)


if __name__ == "__main__":
    unittest.main()


# The shape of presentation_design.py on 14 Oct: live had dropped the USD check
# the deployed commit has, and the change edits the line just above it.
BASE = b"def f(payload):\n    ms = payload.get('ms')\n    if ms:\n        build(payload)\n    else:\n        check(payload)\n    holdings = sorted(payload)\n    return holdings\n"
LIVE = b"def f(payload):\n    ms = payload.get('ms')\n    if ms:\n        build(payload)\n    holdings = sorted(payload)\n    return holdings\n"
NEW = BASE.replace(b"    if ms:\n", b"    if composite(payload):\n        ms = True\n    elif ms:\n")


class MergeWithLiveTest(unittest.TestCase):
    def apply(self, folder, before, after, live):
        import tempfile
        pkg = Path(folder) / "src" / "ibkr_etl"
        pkg.mkdir(parents=True, exist_ok=True)
        (pkg / "p.py").write_bytes(live)
        with tempfile.TemporaryDirectory() as tmp:
            for side, data in (("a", before), ("b", after)):
                (Path(tmp) / side / "src" / "ibkr_etl").mkdir(parents=True)
                (Path(tmp) / side / "src" / "ibkr_etl" / "p.py").write_bytes(data)
            diff = subprocess.run(["git", "diff", "--no-index", "--no-prefix", "a", "b"], cwd=tmp, capture_output=True).stdout
        (Path(folder) / "change.patch").write_bytes(diff)
        return subprocess.run(["patch", "-p1", "--forward", "--fuzz=0", "--no-backup-if-mismatch", "-i", "change.patch"],
                              cwd=folder, capture_output=True, text=True), pkg / "p.py"

    def test_the_change_next_to_a_live_edit_is_merged_and_the_live_edit_kept(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            applied, _ = self.apply(tmp, BASE, NEW, LIVE)
            self.assertNotEqual(applied.returncode, 0)  # what run 26 hit
        merged = release.merge_live("p.py", LIVE, BASE, NEW)
        self.assertIn(b"if composite(payload):", merged)
        self.assertNotIn(b"check(payload)", merged)
        self.assertIn("-    else:\n-        check(payload)\n", release.kept_live_edits("p.py", merged, NEW))
        # The second build starts from the exact live copy, so its patch applies cleanly.
        with tempfile.TemporaryDirectory() as tmp:
            applied, path = self.apply(tmp, LIVE, merged, LIVE)
            self.assertEqual(applied.returncode, 0, applied.stdout + applied.stderr)
            self.assertEqual(path.read_bytes(), merged)

    def test_a_live_copy_that_already_has_the_change_merges_to_the_reviewed_file(self):
        self.assertEqual(release.merge_live("p.py", NEW, BASE, NEW), NEW)

    def test_a_conflict_with_a_live_edit_stops_the_release(self):
        live = BASE.replace(b"    if ms:\n", b"    if ms and payload:\n")
        with self.assertRaises(SystemExit) as stopped:
            release.merge_live("p.py", live, BASE, NEW)
        self.assertIn("conflicts with a live-only edit", str(stopped.exception))

    def test_the_build_merges_rejected_files_and_builds_once_more(self):
        import json, tempfile, os
        state = {"head": HEAD, "deployed_commit": DEPLOYED, "changed_files": ["src/ibkr_etl/p.py"],
                 "live_image": release.REG + "/ibkr-platform:web-" + DEPLOYED}
        calls = []

        def overlay(state, merged=None):
            calls.append(merged)
            return ["p.py"] if merged is None else []

        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path("release-state.json").write_text(json.dumps(state))
                with mock.patch.object(release, "overlay_build", side_effect=overlay), \
                        mock.patch.object(release, "sides", return_value=({"p.py": BASE}, {"p.py": NEW})), \
                        mock.patch.object(release, "capture_files", return_value={"src/ibkr_etl/p.py": LIVE}) as capture:
                    release.build()
                saved = json.loads(Path("release-state.json").read_text())
            finally:
                os.chdir(cwd)
        capture.assert_called_once_with(state["live_image"], ["src/ibkr_etl/p.py"])
        self.assertEqual(calls[0], None)
        self.assertEqual(calls[1]["p.py"], (LIVE, release.merge_live("p.py", LIVE, BASE, NEW)))
        self.assertEqual(saved["merged_with_live"], ["p.py"])

    def test_a_second_refusal_stops_the_release(self):
        import json, tempfile, os
        state = {"head": HEAD, "deployed_commit": DEPLOYED, "changed_files": ["src/ibkr_etl/p.py"],
                 "live_image": release.REG + "/ibkr-platform:web-" + DEPLOYED}
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            try:
                Path("release-state.json").write_text(json.dumps(state))
                with mock.patch.object(release, "overlay_build", return_value=["p.py"]), \
                        mock.patch.object(release, "sides", return_value=({"p.py": BASE}, {"p.py": NEW})), \
                        mock.patch.object(release, "capture_files", return_value={"src/ibkr_etl/p.py": LIVE}), \
                        self.assertRaises(SystemExit):
                    release.build()
            finally:
                os.chdir(cwd)

    def test_change_set_uses_the_live_copy_and_the_merge_for_merged_files(self):
        merged = release.merge_live("p.py", LIVE, BASE, NEW)
        with mock.patch.object(release, "sides", return_value=({"p.py": BASE}, {"p.py": NEW})):
            patch, before, after = release.change_set({}, {"p.py": (LIVE, merged)})
        self.assertEqual(before["p.py"], sha(LIVE))
        self.assertEqual(after["p.py"], sha(merged))
