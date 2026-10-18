import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("website_release", ROOT / "deploy" / "website_release.py")
release = importlib.util.module_from_spec(spec)
spec.loader.exec_module(release)

HEAD = "b" * 40
LIVE = "a" * 40
SQL = b"create 073\n"
SHADOW = ("overview=shadow,holdings=shadow,cash=shadow,performance=shadow,risk-snapshot=shadow,"
          "risk-statistics=shadow,combined-holdings=shadow,charts=shadow")


def at(text):
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc)


class PagesValueTest(unittest.TestCase):
    def test_stages(self):
        self.assertEqual(release.pages_value("off"), "off")
        self.assertEqual(release.pages_value("shadow"), SHADOW)
        self.assertEqual(release.pages_value("batch-1"), SHADOW.replace("overview=shadow", "overview=on")
                         .replace("holdings=shadow,cash", "holdings=on,cash").replace("cash=shadow", "cash=on"))
        modes = release.page_modes(release.pages_value("batch-2"))
        self.assertEqual([p for p, m in modes.items() if m == "on"], ["overview", "holdings", "cash", "performance"])
        modes = release.page_modes(release.pages_value("batch-3"))
        self.assertEqual([p for p, m in modes.items() if m != "on"], ["charts"])
        self.assertEqual(modes["charts"], "shadow")
        self.assertEqual(set(release.page_modes(release.pages_value("batch-4")).values()), {"on"})

    def test_the_stages_follow_the_register(self):
        # The same pages and modes the website reads (ibkr_etl.certified_figures.page_modes).
        sys.path.insert(0, str(ROOT / "src"))
        from ibkr_etl import certified_figures as cf
        for stage in release.PAGES_STAGES:
            self.assertEqual(release.page_modes(release.pages_value(stage)), cf.page_modes(release.pages_value(stage)))
        self.assertEqual(release.register_pages(), {page.id: page.batch for page in cf.pages().values()})

    def test_quiet_while_the_daily_runs_publish(self):
        self.assertIsNone(release.pages_blocked(at("2026-10-22T07:49"), "off", SHADOW))
        self.assertIn("daily runs", release.pages_blocked(at("2026-10-22T07:50"), "off", SHADOW))
        self.assertIn("daily runs", release.pages_blocked(at("2026-10-22T09:24"), "off", SHADOW))
        self.assertIsNone(release.pages_blocked(at("2026-10-22T09:25"), "off", SHADOW))

    def test_month_end_only_moves_pages_back(self):
        on = release.pages_value("batch-4")
        self.assertIn("month-end", release.pages_blocked(at("2026-11-03T12:00"), SHADOW, release.pages_value("batch-1")))
        self.assertIsNone(release.pages_blocked(at("2026-11-03T12:00"), on, release.pages_value("batch-2")))
        self.assertIsNone(release.pages_blocked(at("2026-11-03T12:00"), on, "off"))
        self.assertIsNone(release.pages_blocked(at("2026-11-08T12:00"), SHADOW, on))


class PagesCommandTest(unittest.TestCase):
    def run_pages(self, stage, previous=None, image="reg/ibkr-platform:web-" + LIVE, readers=True, applied=True,
                  now="2026-10-22T12:00"):
        env = [{"name": release.PAGES_SETTING, "value": previous}] if previous is not None else []
        app = {"properties": {"template": {"containers": [{"image": image, "env": env}]},
                              "configuration": {"ingress": {"fqdn": "web.example"}}}}
        calls, checked = [], []

        def az(*args, parse=True):
            calls.append(args)
            return app if args[1] == "show" else ""

        def git(*args, binary=False):
            if args[0] == "rev-parse":
                return HEAD + "\n"
            if args[:2] == ("show", HEAD + ":migrations/postgres/073_certified_figures.sql"):
                return SQL
            raise AssertionError(args)

        def run(args, **kwargs):
            self.assertEqual(args[:3], ["git", "cat-file", "-e"])
            self.assertIn(args[3], (LIVE + ":" + release.PAGES_READERS, LIVE + ":" + release.CHARTS_READERS))
            checked.append(args[3].split(":", 1)[1])
            return mock.Mock(returncode=0 if readers is True or args[3].endswith(str(readers)) else 1)

        recorded = {"073_certified_figures.sql": {"sha256": hashlib.sha256(SQL).hexdigest()}} if applied else {}
        clock = mock.Mock(wraps=datetime)
        clock.now.return_value = at(now)
        with tempfile.TemporaryDirectory() as folder:
            record = Path(folder) / "applied-migrations.json"
            record.write_text(json.dumps({"migrations": recorded}))
            with mock.patch.object(release, "az", az), mock.patch.object(release, "git", git), \
                    mock.patch.object(release.subprocess, "run", run), \
                    mock.patch.object(release, "datetime", clock), \
                    mock.patch.object(release, "APPLIED_MIGRATIONS", record), \
                    mock.patch.object(release, "STATE", Path(folder) / "release-state.json"), \
                    mock.patch.object(sys, "argv", ["website_release.py", "pages", stage]), \
                    mock.patch("builtins.print"):
                release.pages()
                state = json.loads(release.STATE.read_text())
        updates = [c for c in calls if c[1] == "update"]
        settings = dict(a.split("=", 1) for a in updates[-1][updates[-1].index("--set-env-vars") + 1:]) if updates else {}
        self.checked = checked
        return settings, state

    def test_shadow_sets_every_page_and_records_the_previous_value(self):
        settings, state = self.run_pages("shadow")
        self.assertEqual(settings, {release.PAGES_SETTING: SHADOW})
        self.assertEqual(state["previous_pages"], "off")
        self.assertEqual(state["changed_files"], [release.PAGES_SETTING])
        self.assertTrue(state["revision"].startswith("ibkrv2stg-web--pages-shadow-"))
        self.assertEqual(state["secrets"], {})

    def test_a_website_without_the_readers_is_refused(self):
        with self.assertRaises(SystemExit):
            self.run_pages("shadow", readers=False)
        with self.assertRaises(SystemExit):
            self.run_pages("shadow", image="reg/ibkr-platform@sha256:" + "c" * 64)

    def test_batch_4_needs_a_website_that_can_read_the_charts(self):
        settings, state = self.run_pages("batch-4", previous=release.pages_value("batch-3"))
        self.assertEqual(release.page_modes(settings[release.PAGES_SETTING])["charts"], "on")
        self.assertEqual(self.checked, [release.PAGES_READERS, release.CHARTS_READERS])
        self.assertEqual(state["previous_pages"], release.pages_value("batch-3"))
        self.run_pages("batch-3", previous=SHADOW)
        self.assertEqual(self.checked, [release.PAGES_READERS])  # the charts stay in shadow: not needed
        with self.assertRaises(SystemExit):   # the live website has the register but not the charts
            self.run_pages("batch-4", previous=release.pages_value("batch-3"), readers="certified_figures_register_data.py")

    def test_073_must_be_recorded_as_applied(self):
        with self.assertRaises(SystemExit):
            self.run_pages("batch-1", previous=SHADOW, applied=False)

    def test_off_needs_neither_and_goes_back(self):
        settings, state = self.run_pages("off", previous=SHADOW, readers=False, applied=False)
        self.assertEqual(settings, {release.PAGES_SETTING: "off"})
        self.assertEqual(state["previous_pages"], SHADOW)

    def test_nothing_changes_when_already_set(self):
        settings, state = self.run_pages("shadow", previous=SHADOW)
        self.assertEqual(settings, {})
        self.assertEqual(state["changed_files"], [])

    def test_refused_while_the_daily_runs_publish(self):
        with self.assertRaises(SystemExit):
            self.run_pages("shadow", now="2026-10-22T08:00")

    def test_unknown_stage_is_refused(self):
        with mock.patch.object(sys, "argv", ["website_release.py", "pages", "everything"]):
            with self.assertRaises(SystemExit):
                release.pages()

    def test_rollback_puts_the_previous_value_back(self):
        calls = []
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "release-state.json"
            state.write_text(json.dumps({"changed_files": [release.PAGES_SETTING], "previous_pages": "off",
                                         "revision": "r", "fqdn": "f"}))
            with mock.patch.object(release, "az", lambda *a, parse=True: calls.append(a)), \
                    mock.patch.object(release, "STATE", state), mock.patch("builtins.print"):
                release.rollback()
        self.assertIn(release.PAGES_SETTING + "=off", calls[-1])


class SecretsStayAsTheyWereTest(unittest.TestCase):
    APP = {"configuration": {"secrets": [{"name": "credential-broker-auth", "keyVaultUrl": "https://vault/secrets/x",
                                          "identity": "id"}, {"name": "database-web-url", "keyVaultUrl": "https://v/y"}]},
           "latestReadyRevisionName": "ibkrv2stg-web--r1"}

    def run_verify(self, secrets_before, app=None):
        app = app or self.APP

        def az(*args, parse=True):
            if args[1] == "show":
                return {"properties": app}
            return {"properties": {"healthState": "Healthy", "runningState": "Running"}}

        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder) / "release-state.json"
            state.write_text(json.dumps({"changed_files": [release.PAGES_SETTING], "revision": "ibkrv2stg-web--r1",
                                         "fqdn": "web.example", "secrets": secrets_before}))
            with mock.patch.object(release, "az", az), mock.patch.object(release, "STATE", state), \
                    mock.patch.object(release, "anonymous_status", return_value=(401, "")), \
                    mock.patch("builtins.print"):
                release.verify()

    def test_kinds_only_never_values(self):
        kinds = release.secret_kinds({"properties": {"configuration": {"secrets": [
            {"name": "a", "keyVaultUrl": "https://v/a"}, {"name": "b", "value": "not-printed"}]}}})
        self.assertEqual(kinds, {"a": "key-vault", "b": "inline"})

    def test_unchanged_secrets_pass(self):
        self.run_verify({"credential-broker-auth": "key-vault", "database-web-url": "key-vault"})

    def test_a_secret_back_inline_fails(self):
        with self.assertRaises(SystemExit) as stopped:
            self.run_verify({"credential-broker-auth": "key-vault", "database-web-url": "key-vault"},
                            app={**self.APP, "configuration": {"secrets": [
                                {"name": "credential-broker-auth", "value": "old"},
                                {"name": "database-web-url", "keyVaultUrl": "https://v/y"}]}})
        self.assertIn("network thread", str(stopped.exception))


class PrepareSeesThePagesSettingTest(unittest.TestCase):
    def test_pages_in_shadow_count_as_switched_on(self):
        self.assertTrue(release.switch_is_on(release.PAGES_SETTING, SHADOW))
        self.assertFalse(release.switch_is_on(release.PAGES_SETTING, "off"))
        self.assertFalse(release.switch_is_on(release.PAGES_SETTING, ""))
        self.assertTrue(release.switch_is_on("ONBOARDING_V2_ENABLED", "True"))


if __name__ == "__main__":
    unittest.main()
